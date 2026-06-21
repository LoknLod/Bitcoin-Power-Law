#!/usr/bin/env python3
"""Refresh local BTC/gold historical ratio cache for the AIM Macro page."""

from __future__ import annotations

import argparse
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "market-history-cache.json"
SCHEMA_VERSION = "market_history_cache.v0.1"
COINGECKO_MARKET_CHART_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart?{query}"


Fetcher = Callable[[str], Dict[str, Any]]


def utc_now_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_day_stamp(as_of: date) -> str:
    return f"{as_of.isoformat()}T00:00:00Z"


def parse_as_of(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--as-of must use YYYY-MM-DD") from exc


def parse_price(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def fetch_json(url: str, timeout: int = 30) -> Dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Bitcoin-Power-Law AIM history cache updater",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON root is not an object")
    return payload


def read_json(path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return None, f"{path.name} not found"
    except json.JSONDecodeError as exc:
        return None, f"{path.name} malformed: {exc}"
    except OSError as exc:
        return None, f"{path.name} unreadable: {exc}"
    if not isinstance(payload, dict):
        return None, f"{path.name} root is not an object"
    return payload, None


def coingecko_chart_url(coin_id: str, days: int) -> str:
    query = urllib.parse.urlencode({"vs_currency": "usd", "days": str(days), "interval": "daily"})
    return COINGECKO_MARKET_CHART_URL.format(coin_id=urllib.parse.quote(coin_id), query=query)


def normalize_price_series(payload: Dict[str, Any]) -> Dict[str, float]:
    prices = payload.get("prices")
    if not isinstance(prices, list):
        raise ValueError("market_chart payload missing prices list")

    series: Dict[str, float] = {}
    for row in prices:
        if not isinstance(row, list) or len(row) < 2:
            continue
        timestamp_ms, raw_price = row[0], row[1]
        price = parse_price(raw_price)
        if price is None:
            continue
        try:
            observed = datetime.fromtimestamp(float(timestamp_ms) / 1000, tz=timezone.utc).date().isoformat()
        except (TypeError, ValueError, OSError, OverflowError):
            continue
        series[observed] = price
    return series


def build_ratio_series(btc_series: Dict[str, float], gold_series: Dict[str, float]) -> List[Dict[str, float]]:
    rows: List[Dict[str, float]] = []
    for day in sorted(set(btc_series).intersection(gold_series)):
        btc_price = btc_series[day]
        gold_price = gold_series[day]
        if gold_price <= 0:
            continue
        rows.append(
            {
                "date": day,
                "btc_usd": round(btc_price, 2),
                "gold_usd": round(gold_price, 2),
                "btc_gold_ratio": round(btc_price / gold_price, 4),
            }
        )
    return rows


def normalize_existing_cache(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ratio_series = payload.get("ratio_series")
    if not isinstance(ratio_series, list) or not ratio_series:
        return None
    return payload


def build_cache(
    output_path: Path,
    as_of: Optional[date],
    days: int,
    offline: bool = False,
    fetcher: Fetcher = fetch_json,
) -> Tuple[Dict[str, Any], Dict[str, int]]:
    generated_at = utc_day_stamp(as_of) if as_of else utc_now_stamp()
    existing_cache, existing_error = read_json(output_path)

    if offline:
        if existing_cache is None:
            raise FileNotFoundError(existing_error or f"{output_path} not found")
        normalized = normalize_existing_cache(existing_cache)
        if normalized is None:
            raise ValueError(f"{output_path.name} missing valid ratio_series")
        normalized["generated_at"] = generated_at
        normalized["source"] = "existing_market_history_cache"
        normalized.setdefault("metadata", {})["offline_replay"] = True
        return normalized, {"rows": len(normalized["ratio_series"]), "errors": 0, "preserved": 1}

    errors: List[str] = []
    btc_series: Dict[str, float] = {}
    gold_series: Dict[str, float] = {}
    for key, coin_id in (("btc_usd", "bitcoin"), ("gold_usd", "pax-gold")):
        try:
            payload = fetcher(coingecko_chart_url(coin_id, days))
            normalized = normalize_price_series(payload)
            if key == "btc_usd":
                btc_series = normalized
            else:
                gold_series = normalized
        except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"CoinGecko {coin_id} market_chart failed: {exc}")

    ratio_series = build_ratio_series(btc_series, gold_series)
    preserved = 0
    if not ratio_series and existing_cache:
        normalized = normalize_existing_cache(existing_cache)
        if normalized is not None:
            ratio_series = normalized["ratio_series"]
            preserved = 1
            errors.append("preserved existing ratio_series after refresh error")

    cache = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "source": "coingecko_market_chart" if ratio_series and not preserved else "existing_market_history_cache",
        "days": days,
        "assets": {
            "btc_usd": {"coin_id": "bitcoin", "source": "CoinGecko market_chart"},
            "gold_usd": {"coin_id": "pax-gold", "source": "CoinGecko market_chart PAXG gold proxy"},
        },
        "ratio_series": ratio_series,
        "errors": errors,
        "metadata": {
            "as_of": as_of.isoformat() if as_of else None,
            "row_count": len(ratio_series),
            "preserved_existing": bool(preserved),
            "error_count": len(errors),
        },
    }
    return cache, {"rows": len(ratio_series), "errors": len(errors), "preserved": preserved}


def write_cache(cache: Dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh local BTC/gold historical ratio cache.")
    parser.add_argument("--as-of", type=parse_as_of, default=None, help="As-of date in YYYY-MM-DD.")
    parser.add_argument("--days", type=int, default=365, help="History window. Defaults to 365 days. Up to 2000 for 200-week MA support.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output path. Defaults to market-history-cache.json.")
    parser.add_argument("--offline", action="store_true", help="Validate and render from existing cache without network access.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.days < 7 or args.days > 2000:
        print("Market history cache update refused: --days must be between 7 and 2000")
        return 2
    output_path = args.output if args.output.is_absolute() else ROOT / args.output
    try:
        cache, stats = build_cache(output_path, args.as_of, args.days, offline=args.offline)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Market history cache update failed: {exc}")
        return 1
    write_cache(cache, output_path)
    mode = "offline" if args.offline else "online"
    print(
        f"Wrote {output_path} | mode={mode} | rows={stats['rows']} | "
        f"preserved={stats['preserved']} | errors={stats['errors']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
