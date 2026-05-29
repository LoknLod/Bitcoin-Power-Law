#!/usr/bin/env python3
"""Refresh the local FRED cache for AIM Macro Cockpit.

The updater intentionally uses FRED's public graph CSV endpoint instead of the
API endpoint so local cache refreshes do not need API keys.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import subprocess
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "fred-cache.json"
SCHEMA_VERSION = "fred_cache.v0.1"
FRED_GRAPH_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"


CURATED_SERIES: Tuple[Dict[str, str], ...] = (
    {
        "id": "WALCL",
        "desc": "Fed Balance Sheet (Weekly, millions USD)",
    },
    {
        "id": "RRPONTSYD",
        "desc": "Overnight Reverse Repo (Daily, billions USD)",
    },
    {
        "id": "WTREGEN",
        "desc": "Treasury General Account (Weekly, millions USD)",
    },
    {
        "id": "WM2NS",
        "desc": "M2 Money Stock (Weekly, billions USD)",
    },
    {
        "id": "QUSCAMUSDA",
        "desc": "U.S. total credit to non-financial sector, adjusted for breaks",
    },
    {
        "id": "Q5ACAMUSDA",
        "desc": "Total reporting countries total credit to non-financial sector, adjusted for breaks",
    },
    {
        "id": "GFDEBTN",
        "desc": "Federal Debt: Total Public Debt",
    },
    {
        "id": "A091RC1Q027SBEA",
        "desc": "Federal government current expenditures: interest payments",
    },
    {
        "id": "DGS10",
        "desc": "10-Year Treasury Constant Maturity Rate",
    },
    {
        "id": "DFII10",
        "desc": "10-Year Treasury Inflation-Indexed Constant Maturity Rate",
    },
    {
        "id": "T10YIE",
        "desc": "10-Year Breakeven Inflation Rate",
    },
    {
        "id": "BAMLH0A0HYM2",
        "desc": "ICE BofA US High Yield Index Option-Adjusted Spread",
    },
)


def utc_now_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_day_stamp(as_of: date) -> str:
    return f"{as_of.isoformat()}T00:00:00Z"


def parse_as_of(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--as-of must use YYYY-MM-DD") from exc


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


def format_number(value: float) -> str:
    return f"{value:.12f}".rstrip("0").rstrip(".")


def find_field(fields: Iterable[str], candidates: Iterable[str]) -> Optional[str]:
    field_map = {field.lower(): field for field in fields}
    for candidate in candidates:
        match = field_map.get(candidate.lower())
        if match is not None:
            return match
    return None


def parse_fred_csv(series_id: str, text: str, through: Optional[date] = None) -> List[Dict[str, str]]:
    """Parse a FRED graph CSV payload into cache observations.

    FRED graph CSVs usually use `observation_date,SERIES`, but this accepts the
    older `DATE` shape too. Missing `.` values are ignored.
    """

    handle = io.StringIO(text.lstrip("\ufeff"))
    reader = csv.DictReader(handle)
    if not reader.fieldnames:
        raise ValueError(f"{series_id}: CSV has no header")

    date_field = find_field(reader.fieldnames, ("observation_date", "date", "DATE"))
    if date_field is None:
        raise ValueError(f"{series_id}: CSV has no date column")

    value_field = find_field(reader.fieldnames, (series_id, "value", "VALUE"))
    if value_field is None:
        value_fields = [field for field in reader.fieldnames if field != date_field]
        if not value_fields:
            raise ValueError(f"{series_id}: CSV has no value column")
        value_field = value_fields[0]

    observations: List[Dict[str, str]] = []
    for row in reader:
        raw_date = (row.get(date_field) or "").strip()
        raw_value = row.get(value_field)
        if not raw_date:
            continue
        try:
            observed_at = date.fromisoformat(raw_date)
        except ValueError:
            continue
        if through is not None and observed_at > through:
            continue
        parsed_value = parse_float(raw_value)
        if parsed_value is None:
            continue
        observations.append({"date": observed_at.isoformat(), "value": format_number(parsed_value)})

    observations.sort(key=lambda item: item["date"])
    return observations


def read_json(path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return None, f"{path.name} not found"
    except json.JSONDecodeError as exc:
        return None, f"{path.name} malformed: {exc}"
    except OSError as exc:
        return None, f"{path.name} unreadable: {exc}"
    if not isinstance(data, dict):
        return None, f"{path.name} root is not an object"
    return data, None


def observations_from_existing(payload: Any, through: Optional[date]) -> List[Dict[str, str]]:
    if isinstance(payload, dict):
        raw_observations = payload.get("observations", [])
    elif isinstance(payload, list):
        raw_observations = payload
    else:
        raw_observations = []

    observations: List[Dict[str, str]] = []
    if not isinstance(raw_observations, list):
        return observations

    for item in raw_observations:
        if not isinstance(item, dict):
            continue
        raw_date = item.get("date") or item.get("observation_date") or item.get("DATE")
        parsed_value = parse_float(item.get("value"))
        if raw_date is None or parsed_value is None:
            continue
        try:
            observed_at = date.fromisoformat(str(raw_date))
        except ValueError:
            continue
        if through is not None and observed_at > through:
            continue
        observations.append({"date": observed_at.isoformat(), "value": format_number(parsed_value)})

    observations.sort(key=lambda item: item["date"])
    return observations


def fetch_text(url: str, timeout: int = 20) -> str:
    """Fetch text using curl first, then urllib as a bounded fallback.

    On this macOS host FRED's CSV endpoint has repeatedly stalled under
    Python's urllib while curl succeeds. Prefer the path that works here, but
    keep urllib available for environments without curl.
    """

    curl_exc: Optional[Exception] = None
    try:
        completed = subprocess.run(
            [
                "curl",
                "-fsSL",
                "--connect-timeout",
                "8",
                "--max-time",
                str(timeout),
                "--retry",
                "1",
                "--retry-delay",
                "1",
                url,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
        return completed.stdout
    except Exception as exc:
        curl_exc = exc

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/csv,*/*",
            "User-Agent": "Bitcoin-Power-Law AIM cache updater",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=min(timeout, 10)) as response:
            return response.read().decode("utf-8-sig")
    except Exception as urllib_exc:
        raise RuntimeError(f"curl failed: {curl_exc}; urllib fallback failed: {urllib_exc}") from urllib_exc


def build_series_payload(
    spec: Dict[str, str],
    observations: List[Dict[str, str]],
    source: str,
    generated_at: str,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    latest_date = observations[-1]["date"] if observations else None
    payload: Dict[str, Any] = {
        "desc": spec["desc"],
        "source": source,
        "url": FRED_GRAPH_URL.format(series_id=spec["id"]),
        "fetched_at": generated_at,
        "observation_count": len(observations),
        "latest_date": latest_date,
        "observations": observations,
    }
    if error:
        payload["error"] = error
    return payload


def build_cache(
    output_path: Path,
    as_of: Optional[date],
    offline: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, int]]:
    generated_at = utc_day_stamp(as_of) if as_of else utc_now_stamp()
    existing_cache, existing_error = read_json(output_path)
    existing_series = existing_cache.get("series", {}) if isinstance(existing_cache, dict) else {}
    if not isinstance(existing_series, dict):
        existing_series = {}

    errors: List[Dict[str, str]] = []
    series_payloads: Dict[str, Dict[str, Any]] = {}
    fetched = 0
    preserved = 0

    if offline and existing_cache is None:
        raise FileNotFoundError(existing_error or f"{output_path} not found")

    fetch_start = None
    if as_of is not None:
        try:
            fetch_start = as_of.replace(year=as_of.year - 6)
        except ValueError:
            fetch_start = as_of.replace(month=2, day=28, year=as_of.year - 6)

    for spec in CURATED_SERIES:
        series_id = spec["id"]
        url = FRED_GRAPH_URL.format(series_id=series_id)
        if fetch_start is not None:
            url = f"{url}&cosd={fetch_start.isoformat()}&coed={as_of.isoformat()}"
        observations: List[Dict[str, str]] = []
        source = "existing_cache" if offline else "fred_graph_csv"
        error: Optional[str] = None

        if offline:
            observations = observations_from_existing(existing_series.get(series_id), as_of)
            if observations:
                preserved += 1
            else:
                error = "series missing or empty in existing cache"
        else:
            try:
                observations = parse_fred_csv(series_id, fetch_text(url), through=as_of)
                if not observations:
                    error = "FRED CSV returned no usable observations"
                else:
                    fetched += 1
            except Exception as exc:
                error = f"refresh failed: {exc}"
                observations = observations_from_existing(existing_series.get(series_id), as_of)
                if observations:
                    source = "existing_cache_after_refresh_error"
                    preserved += 1

        if error:
            errors.append({"series": series_id, "error": error})
        if observations:
            series_payloads[series_id] = build_series_payload(
                spec,
                observations,
                source,
                generated_at,
                error=error if source.startswith("existing_cache_after") else None,
            )
        else:
            series_payloads[series_id] = build_series_payload(
                spec,
                [],
                source,
                generated_at,
                error=error,
            )

    observation_count = sum(payload["observation_count"] for payload in series_payloads.values())
    cache = {
        "schema_version": SCHEMA_VERSION,
        "generated": generated_at,
        "generated_at": generated_at,
        "source": "existing_cache" if offline else "fred_graph_csv",
        "metadata": {
            "as_of": as_of.isoformat() if as_of else None,
            "series_requested": len(CURATED_SERIES),
            "series_fetched": fetched,
            "series_preserved": preserved,
            "series_with_errors": len(errors),
            "observation_count": observation_count,
            "endpoint": FRED_GRAPH_URL,
        },
        "series": series_payloads,
        "errors": errors,
    }
    stats = {
        "fetched": fetched,
        "preserved": preserved,
        "observations": observation_count,
        "errors": len(errors),
    }
    return cache, stats


def write_cache(cache: Dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh the local AIM FRED cache.")
    parser.add_argument(
        "--as-of",
        type=parse_as_of,
        default=None,
        help="Deterministic as-of date in YYYY-MM-DD. Also filters future observations.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output cache path. Defaults to fred-cache.json in the repo root.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Validate and render from the existing cache without network access.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_path = args.output if args.output.is_absolute() else ROOT / args.output
    try:
        cache, stats = build_cache(output_path, args.as_of, offline=args.offline)
    except FileNotFoundError as exc:
        print(f"FRED cache update failed: {exc}")
        return 1

    write_cache(cache, output_path)
    mode = "offline" if args.offline else "online"
    print(
        f"Wrote {output_path} | mode={mode} | "
        f"series_fetched={stats['fetched']} | "
        f"series_preserved={stats['preserved']} | "
        f"observations={stats['observations']} | "
        f"errors={stats['errors']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
