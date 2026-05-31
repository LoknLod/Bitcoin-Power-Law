#!/usr/bin/env python3
"""Refresh local EIA energy cache for AIM energy bottleneck signals."""

from __future__ import annotations

import argparse
import json
import math
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from itertools import cycle
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "energy-cache.json"
SCHEMA_VERSION = "eia_energy_cache.v0.1"
EIA_RETAIL_SALES_URL = "https://api.eia.gov/v2/electricity/retail-sales/data/"
CURATED_SERIES: Tuple[Dict[str, str], ...] = (
    {
        "key": "total_electricity",
        "name": "all sectors",
        "sector_id": "ALL",
        "description": "U.S. all-sector retail electricity price and sales",
    },
    {
        "key": "commercial_electricity",
        "name": "commercial",
        "sector_id": "COM",
        "description": "U.S. commercial electricity price and sales; closest broad proxy for data-center load pressure",
    },
    {
        "key": "industrial_electricity",
        "name": "industrial",
        "sector_id": "IND",
        "description": "U.S. industrial electricity price and sales; broad heavy-load demand context",
    },
)
FetchFn = Callable[[Dict[str, str], str], Dict[str, Any]]


def utc_now_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def collect_api_keys(env: Optional[Dict[str, str]] = None) -> List[str]:
    source = env if env is not None else os.environ
    names = ["EIA_API", "EIA_API_KEY"]
    keys: List[str] = []
    seen = set()
    for name in names:
        value = str(source.get(name) or "").strip()
        if not value or value in seen:
            continue
        keys.append(value)
        seen.add(value)
    return keys


def parse_float(value: Any) -> Optional[float]:
    if value in (None, "", "."):
        return None
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def sanitize_error_message(message: str, api_keys: Iterable[str]) -> str:
    sanitized = str(message)
    for key in api_keys:
        if key:
            sanitized = sanitized.replace(key, "[REDACTED]")
    return sanitized


def normalize_electricity_rows(rows: List[Dict[str, Any]], source_series: str) -> List[Dict[str, Any]]:
    observations: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        period = str(row.get("period") or "").strip()
        if not period:
            continue
        price = parse_float(row.get("price"))
        sales = parse_float(row.get("sales"))
        if price is None and sales is None:
            continue
        observation: Dict[str, Any] = {
            "date": period,
            "sector_id": str(row.get("sectorid") or "").strip(),
            "sector_name": str(row.get("sectorName") or "").strip(),
            "source_series": source_series,
        }
        if price is not None:
            observation["price_cents_per_kwh"] = price
        if sales is not None:
            observation["sales_million_kwh"] = sales
        observations.append(observation)
    observations.sort(key=lambda item: item["date"], reverse=True)
    return observations


def fetch_eia_series(series: Dict[str, str], api_key: str, months: int = 36, timeout: int = 30) -> Dict[str, Any]:
    params = [
        ("api_key", api_key),
        ("frequency", "monthly"),
        ("data[0]", "price"),
        ("data[1]", "sales"),
        ("facets[stateid][]", "US"),
        ("facets[sectorid][]", series["sector_id"]),
        ("sort[0][column]", "period"),
        ("sort[0][direction]", "desc"),
        ("length", str(months)),
    ]
    request = urllib.request.Request(
        f"{EIA_RETAIL_SALES_URL}?{urllib.parse.urlencode(params)}",
        headers={
            "Accept": "application/json",
            "User-Agent": "Bitcoin-Power-Law AIM EIA cache updater",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("EIA JSON root is not an object")
    return payload


def extract_eia_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    response = payload.get("response")
    if not isinstance(response, dict):
        raise ValueError("EIA response is missing response object")
    rows = response.get("data")
    if not isinstance(rows, list):
        raise ValueError("EIA response is missing data rows")
    return rows


def build_cache(
    api_keys: List[str],
    fetcher: FetchFn = fetch_eia_series,
    sleep_seconds: float = 0.25,
    generated_at: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, int]]:
    if not api_keys:
        raise ValueError("No EIA API keys available")

    generated_at = generated_at or utc_now_stamp()
    key_cycle = cycle(api_keys)
    cache_series: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []
    updated_series = 0
    request_count = 0

    for series in CURATED_SERIES:
        api_key = next(key_cycle)
        series_key = series["key"]
        payload = {
            "description": series["description"],
            "sector_id": series["sector_id"],
            "observations": [],
            "errors": [],
        }
        try:
            raw = fetcher(series, api_key)
            request_count += 1
            rows = extract_eia_rows(raw)
            payload["observations"] = normalize_electricity_rows(rows, series_key)
            if payload["observations"]:
                updated_series += 1
        except Exception as exc:  # pragma: no cover - network exception type varies by platform
            message = sanitize_error_message(str(exc), api_keys)
            payload["errors"].append(message)
            errors.append(f"{series_key}: {message}")
        cache_series[series_key] = payload
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    cache = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "source": "eia_api",
        "series": cache_series,
        "errors": errors,
        "metadata": {
            "series_count": len(cache_series),
            "updated_series": updated_series,
            "error_count": len(errors),
            "request_count": request_count,
            "curated_series": [series["key"] for series in CURATED_SERIES],
            "endpoint": "electricity/retail-sales",
        },
    }
    stats = {
        "series_count": len(cache_series),
        "updated_series": updated_series,
        "error_count": len(errors),
        "request_count": request_count,
    }
    return cache, stats


def write_cache(cache: Dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(cache, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh EIA energy cache for AIM bottleneck scoring")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output JSON path")
    parser.add_argument("--sleep-seconds", type=float, default=0.25, help="Delay between EIA requests")
    args = parser.parse_args()

    api_keys = collect_api_keys()
    if not api_keys:
        raise SystemExit("No EIA API key found; set EIA_API or EIA_API_KEY")

    cache, stats = build_cache(api_keys, sleep_seconds=args.sleep_seconds)
    write_cache(cache, args.output)
    print(
        f"wrote {args.output} series={stats['series_count']} updated={stats['updated_series']} "
        f"errors={stats['error_count']} requests={stats['request_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
