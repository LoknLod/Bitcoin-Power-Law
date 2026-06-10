"""Pins the market-cache failure-mode contract the host refresh wrapper relies on.

shrike_btc_widget_price_refresh.sh (host side) validates the freshly built
cache by checking that btc_usd and gold_usd prices are finite and > 0. That
only works because update_market_cache.py writes price-0 placeholders when an
asset failed on every provider. These tests pin that contract.
"""
from __future__ import annotations

import importlib.util
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "update_market_cache", ROOT / "scripts" / "update_market_cache.py"
)
umc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(umc)


class PlaceholderContractTests(unittest.TestCase):
    def test_placeholder_assets_have_zero_price(self):
        for key in ("btc_usd", "gold_usd"):
            placeholder = umc.placeholder_asset(key, "2026-06-10T00:00:00Z")
            self.assertEqual(placeholder["price"], 0)
            self.assertEqual(placeholder["provider"], "missing")
            # parse_price treats 0 as invalid, so downstream validation
            # (wrapper price > 0 check) rejects placeholders.
            self.assertIsNone(umc.parse_price(placeholder["price"]))

    def test_parse_price_rejects_non_positive_and_non_finite(self):
        for bad in (0, -1, "0", "", None, ".", "nan", "inf", float("nan"), float("inf")):
            self.assertIsNone(umc.parse_price(bad), f"parse_price accepted {bad!r}")
        self.assertEqual(umc.parse_price("61865.0"), 61865.0)

    def test_normalize_existing_asset_rejects_priceless_payloads(self):
        self.assertIsNone(umc.normalize_existing_asset({"price": 0}))
        self.assertIsNone(umc.normalize_existing_asset({"source": "x"}))
        normalized = umc.normalize_existing_asset({"price": 100.0, "source": "s"})
        self.assertEqual(normalized["price"], 100.0)


class OfflineModeTests(unittest.TestCase):
    def test_offline_build_preserves_existing_assets_without_network(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "market-cache.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "market_cache.v0.1",
                        "generated_at": "2026-06-09T00:00:00Z",
                        "assets": {
                            "btc_usd": {"price": 61865.0, "source": "s", "label": "l", "provider": "p", "as_of": "2026-06-09T00:00:00Z"},
                            "gold_usd": {"price": 4207.71, "source": "s", "label": "l", "provider": "p", "as_of": "2026-06-09T00:00:00Z"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            cache, stats = umc.build_cache(path, date(2026, 6, 9), offline=True)

        self.assertEqual(stats["preserved"], 2)
        self.assertEqual(stats["valid_assets"], 2)
        self.assertEqual(cache["assets"]["btc_usd"]["price"], 61865.0)


if __name__ == "__main__":
    unittest.main()
