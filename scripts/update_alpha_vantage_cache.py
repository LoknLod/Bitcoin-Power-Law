#!/usr/bin/env python3
"""Refresh the local Alpha Vantage fundamentals cache for AIM AI signals."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from itertools import cycle
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "alpha-vantage-cache.json"
SCHEMA_VERSION = "alpha_vantage_cache.v0.1"
DEFAULT_TICKERS = ["MSFT", "GOOGL", "AMZN", "META", "NVDA", "AVGO", "AMD", "ORCL", "TSM"]
ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
FUNCTIONS = ["OVERVIEW", "INCOME_STATEMENT", "BALANCE_SHEET", "CASH_FLOW", "EARNINGS"]
FUNCTION_CACHE_KEYS = {
    "OVERVIEW": "overview",
    "INCOME_STATEMENT": "income_statement",
    "BALANCE_SHEET": "balance_sheet",
    "CASH_FLOW": "cash_flow",
    "EARNINGS": "earnings",
}
EMPTY_PAYLOADS = {
    "OVERVIEW": {},
    "INCOME_STATEMENT": {"annualReports": [], "quarterlyReports": []},
    "BALANCE_SHEET": {"annualReports": [], "quarterlyReports": []},
    "CASH_FLOW": {"annualReports": [], "quarterlyReports": []},
    "EARNINGS": {"annualEarnings": [], "quarterlyEarnings": []},
}
ERROR_FIELDS = ("Note", "Information", "Error Message")
FetchFn = Callable[[str, str, str], Dict[str, Any]]


def utc_now_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_tickers(value: str) -> List[str]:
    tickers = [part.strip().upper() for part in value.split(",") if part.strip()]
    if not tickers:
        raise argparse.ArgumentTypeError("--tickers must include at least one symbol")
    return tickers


def collect_api_keys(env: Optional[Dict[str, str]] = None) -> List[str]:
    source = env if env is not None else os.environ
    names = ["ALPHA_VANTAGE_STOCK_API", "ALPHAVANTAGE_API_KEY"]
    keys: List[str] = []
    seen = set()
    for name in names:
        value = str(source.get(name) or "").strip()
        if not value or value in seen:
            continue
        keys.append(value)
        seen.add(value)
    return keys


def cache_key_for_function(function: str) -> str:
    return FUNCTION_CACHE_KEYS[function]


def empty_payload_for_function(function: str) -> Dict[str, Any]:
    return json.loads(json.dumps(EMPTY_PAYLOADS[function]))


def sanitize_error_message(message: str, api_keys: Iterable[str]) -> str:
    sanitized = str(message)
    for key in api_keys:
        if key:
            sanitized = sanitized.replace(key, "[REDACTED]")
    return sanitized


def alpha_vantage_error(payload: Dict[str, Any], api_keys: Iterable[str]) -> Optional[str]:
    for field in ERROR_FIELDS:
        if field in payload:
            return sanitize_error_message(str(payload[field]), api_keys)
    return None


def fetch_alpha_vantage_json(function: str, symbol: str, api_key: str, timeout: int = 30) -> Dict[str, Any]:
    query = urllib.parse.urlencode({"function": function, "symbol": symbol, "apikey": api_key})
    request = urllib.request.Request(
        f"{ALPHA_VANTAGE_URL}?{query}",
        headers={
            "Accept": "application/json",
            "User-Agent": "Bitcoin-Power-Law AIM Alpha Vantage cache updater",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Alpha Vantage JSON root is not an object")
    return payload


def normalize_function_payload(function: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if function == "OVERVIEW":
        return dict(payload)
    if function == "EARNINGS":
        return {
            "annualEarnings": list(payload.get("annualEarnings") or []),
            "quarterlyEarnings": list(payload.get("quarterlyEarnings") or []),
        }
    return {
        "annualReports": list(payload.get("annualReports") or []),
        "quarterlyReports": list(payload.get("quarterlyReports") or []),
    }


def blank_ticker_payload() -> Dict[str, Any]:
    payload = {cache_key_for_function(function): empty_payload_for_function(function) for function in FUNCTIONS}
    payload["errors"] = []
    return payload


def build_cache(
    tickers: List[str],
    api_keys: List[str],
    fetcher: FetchFn = fetch_alpha_vantage_json,
    sleep_seconds: float = 12.0,
    generated_at: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, int]]:
    if not api_keys:
        raise ValueError("No Alpha Vantage API keys available")

    generated_at = generated_at or utc_now_stamp()
    key_cycle = cycle(api_keys)
    cache_tickers: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []
    updated_tickers = 0
    request_count = 0

    for raw_symbol in tickers:
        symbol = raw_symbol.strip().upper()
        if not symbol:
            continue
        ticker_payload = blank_ticker_payload()
        ticker_had_success = False

        for function in FUNCTIONS:
            api_key = next(key_cycle)
            try:
                payload = fetcher(function, symbol, api_key)
                request_count += 1
                error_message = alpha_vantage_error(payload, api_keys)
                if error_message:
                    ticker_payload["errors"].append(f"{function}: {error_message}")
                    ticker_payload[cache_key_for_function(function)] = empty_payload_for_function(function)
                else:
                    ticker_payload[cache_key_for_function(function)] = normalize_function_payload(function, payload)
                    ticker_had_success = True
            except Exception as exc:  # pragma: no cover - exact network exception type varies by platform
                ticker_payload["errors"].append(f"{function}: {sanitize_error_message(str(exc), api_keys)}")
                ticker_payload[cache_key_for_function(function)] = empty_payload_for_function(function)
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

        if ticker_had_success:
            updated_tickers += 1
        cache_tickers[symbol] = ticker_payload
        for error in ticker_payload["errors"]:
            errors.append(f"{symbol} {error}")

    cache = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "source": "alpha_vantage",
        "tickers": cache_tickers,
        "errors": errors,
        "metadata": {
            "ticker_count": len(cache_tickers),
            "updated_tickers": updated_tickers,
            "error_count": len(errors),
            "request_count": request_count,
            "functions": FUNCTIONS,
        },
    }
    stats = {
        "ticker_count": len(cache_tickers),
        "updated_tickers": updated_tickers,
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
    parser = argparse.ArgumentParser(description="Refresh Alpha Vantage fundamentals cache for AIM AI signals")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output JSON path")
    parser.add_argument("--tickers", type=parse_tickers, default=DEFAULT_TICKERS, help="Comma-separated symbols")
    parser.add_argument("--sleep-seconds", type=float, default=12.0, help="Delay between Alpha Vantage requests")
    args = parser.parse_args()

    api_keys = collect_api_keys()
    if not api_keys:
        raise SystemExit("No Alpha Vantage API keys found; set ALPHA_VANTAGE_STOCK_API or ALPHAVANTAGE_API_KEY")

    cache, stats = build_cache(args.tickers, api_keys, sleep_seconds=args.sleep_seconds)
    write_cache(cache, args.output)
    print(
        f"wrote {args.output} tickers={stats['ticker_count']} updated={stats['updated_tickers']} "
        f"errors={stats['error_count']} requests={stats['request_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
