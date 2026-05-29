#!/usr/bin/env python3
"""Refresh the local BTC/gold market cache for AIM Macro Cockpit."""

from __future__ import annotations

import argparse
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "market-cache.json"
SCHEMA_VERSION = "market_cache.v0.1"
COINGECKO_URL = (
    "https://api.coingecko.com/api/v3/simple/price?"
    + urllib.parse.urlencode(
        {
            "ids": "bitcoin,pax-gold",
            "vs_currencies": "usd",
            "include_last_updated_at": "true",
        }
    )
)
COINBASE_SPOT_URL = "https://api.coinbase.com/v2/prices/{product}/spot"


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
    if value in (None, "", "."):
        return None
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def iso_from_unix_timestamp(value: Any, fallback: str) -> str:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    try:
        return datetime.fromtimestamp(parsed, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except (OSError, OverflowError, ValueError):
        return fallback


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


def fetch_json(url: str, timeout: int = 20) -> Dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Bitcoin-Power-Law AIM cache updater",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON root is not an object")
    return payload


def parse_coingecko_simple_price(payload: Dict[str, Any], generated_at: str) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    assets: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []
    mapping = {
        "btc_usd": ("bitcoin", "BTC/USD spot", "coingecko bitcoin"),
        "gold_usd": ("pax-gold", "pax-gold proxy", "pax-gold proxy"),
    }

    for asset_key, (coingecko_id, label, source) in mapping.items():
        item = payload.get(coingecko_id)
        if not isinstance(item, dict):
            errors.append(f"{coingecko_id} missing from CoinGecko response")
            continue
        price = parse_price(item.get("usd"))
        if price is None:
            errors.append(f"{coingecko_id} USD price missing from CoinGecko response")
            continue
        assets[asset_key] = {
            "price": price,
            "source": source,
            "label": label,
            "provider": "coingecko",
            "as_of": iso_from_unix_timestamp(item.get("last_updated_at"), generated_at),
        }

    return assets, errors


def parse_coinbase_spot(payload: Dict[str, Any]) -> Optional[float]:
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    return parse_price(data.get("amount"))


def coinbase_asset_payload(asset_key: str, price: float, generated_at: str) -> Dict[str, Any]:
    if asset_key == "btc_usd":
        return {
            "price": price,
            "source": "coinbase BTC-USD spot",
            "label": "BTC/USD spot",
            "provider": "coinbase",
            "as_of": generated_at,
        }
    return {
        "price": price,
        "source": "pax-gold proxy",
        "label": "PAXG/USD spot proxy",
        "provider": "coinbase",
        "as_of": generated_at,
    }


def normalize_existing_asset(payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    price = parse_price(payload.get("price"))
    if price is None:
        return None
    return {
        "price": price,
        "source": str(payload.get("source") or "existing_cache"),
        "label": str(payload.get("label") or payload.get("source") or "existing cache"),
        "provider": str(payload.get("provider") or "existing_cache"),
        "as_of": str(payload.get("as_of") or payload.get("generated_at") or ""),
    }


def placeholder_asset(asset_key: str, generated_at: str) -> Dict[str, Any]:
    if asset_key == "btc_usd":
        return {"price": 0, "source": "missing", "label": "BTC/USD spot", "provider": "missing", "as_of": generated_at}
    return {"price": 0, "source": "pax-gold proxy", "label": "PAXG/USD spot proxy", "provider": "missing", "as_of": generated_at}


def source_label(assets: Dict[str, Dict[str, Any]], offline: bool) -> str:
    if offline:
        return "existing_market_cache"
    providers = {asset.get("provider") for asset in assets.values() if parse_price(asset.get("price")) is not None}
    if providers == {"coingecko"}:
        return "coingecko_simple_price"
    if providers == {"coinbase"}:
        return "coinbase_spot"
    if providers:
        return "mixed_public_market_sources"
    return "unavailable"


def build_cache(output_path: Path, as_of: Optional[date], offline: bool = False) -> Tuple[Dict[str, Any], Dict[str, int]]:
    generated_at = utc_day_stamp(as_of) if as_of else utc_now_stamp()
    existing_cache, existing_error = read_json(output_path)
    existing_assets = existing_cache.get("assets", {}) if isinstance(existing_cache, dict) else {}
    if not isinstance(existing_assets, dict):
        existing_assets = {}

    if offline and existing_cache is None:
        raise FileNotFoundError(existing_error or f"{output_path} not found")

    assets: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []
    updated = 0
    preserved = 0

    if offline:
        for asset_key in ("btc_usd", "gold_usd"):
            normalized = normalize_existing_asset(existing_assets.get(asset_key))
            if normalized is None:
                errors.append(f"{asset_key} missing or invalid in existing cache")
                assets[asset_key] = placeholder_asset(asset_key, generated_at)
            else:
                assets[asset_key] = normalized
                preserved += 1
    else:
        try:
            coingecko_assets, coingecko_errors = parse_coingecko_simple_price(fetch_json(COINGECKO_URL), generated_at)
            assets.update(coingecko_assets)
            errors.extend(coingecko_errors)
            updated += len(coingecko_assets)
        except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"CoinGecko refresh failed: {exc}")

        coinbase_products = {"btc_usd": "BTC-USD", "gold_usd": "PAXG-USD"}
        for asset_key, product in coinbase_products.items():
            if asset_key in assets:
                continue
            try:
                price = parse_coinbase_spot(fetch_json(COINBASE_SPOT_URL.format(product=product)))
                if price is None:
                    raise ValueError(f"Coinbase {product} spot price missing")
                assets[asset_key] = coinbase_asset_payload(asset_key, price, generated_at)
                updated += 1
            except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
                errors.append(f"Coinbase {product} refresh failed: {exc}")
                normalized = normalize_existing_asset(existing_assets.get(asset_key))
                if normalized is not None:
                    normalized["source"] = f"{normalized['source']} (existing after refresh error)"
                    assets[asset_key] = normalized
                    preserved += 1

        for asset_key in ("btc_usd", "gold_usd"):
            if asset_key not in assets:
                assets[asset_key] = placeholder_asset(asset_key, generated_at)

    cache = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "source": source_label(assets, offline),
        "assets": assets,
        "errors": errors,
        "metadata": {
            "as_of": as_of.isoformat() if as_of else None,
            "updated_assets": updated,
            "preserved_assets": preserved,
            "error_count": len(errors),
        },
    }
    stats = {
        "updated": updated,
        "preserved": preserved,
        "errors": len(errors),
        "valid_assets": sum(1 for asset in assets.values() if parse_price(asset.get("price")) is not None),
    }
    return cache, stats


def write_cache(cache: Dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh the local AIM BTC/gold market cache.")
    parser.add_argument(
        "--as-of",
        type=parse_as_of,
        default=None,
        help=(
            "As-of date in YYYY-MM-DD. Online refresh only supports today's UTC date "
            "because public price APIs return live spot prices, not historical prices."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output cache path. Defaults to market-cache.json in the repo root.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Validate and render from the existing cache without network access.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    today_utc = datetime.now(timezone.utc).date()
    if args.as_of is not None and not args.offline and args.as_of != today_utc:
        print(
            "Market cache update refused: --as-of with online live prices must equal today's UTC date "
            f"({today_utc.isoformat()}). Use --offline to re-render an existing historical cache."
        )
        return 2

    output_path = args.output if args.output.is_absolute() else ROOT / args.output
    try:
        cache, stats = build_cache(output_path, args.as_of, offline=args.offline)
    except FileNotFoundError as exc:
        print(f"Market cache update failed: {exc}")
        return 1

    write_cache(cache, output_path)
    mode = "offline" if args.offline else "online"
    print(
        f"Wrote {output_path} | mode={mode} | "
        f"assets_updated={stats['updated']} | "
        f"assets_preserved={stats['preserved']} | "
        f"valid_assets={stats['valid_assets']} | "
        f"errors={stats['errors']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
