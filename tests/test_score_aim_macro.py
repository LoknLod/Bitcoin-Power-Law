import json
import math
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import score_aim_macro  # noqa: E402


def series(*rows):
    return {"observations": [{"date": observed_at, "value": str(value)} for observed_at, value in rows]}


class ScoreAimMacroTests(unittest.TestCase):
    def test_btc_power_law_fair_value_is_positive_for_2026_05_29(self):
        value = score_aim_macro.btc_power_law_fair_value(date(2026, 5, 29))

        self.assertIsNotNone(value)
        self.assertTrue(math.isfinite(value))
        self.assertGreater(value, 0)

    def test_build_cache_from_local_fixtures_without_network(self):
        as_of = date(2026, 5, 29)
        fred_cache = {
            "series": {
                "WM2NS": series(("2025-05-26", 21500), ("2026-05-25", 22500)),
                "WALCL": series(("2025-05-28", 6700000), ("2026-02-25", 6650000), ("2026-05-27", 6600000)),
                "RRPONTSYD": series(("2026-02-27", 90), ("2026-05-28", 25)),
                "WTREGEN": series(("2026-02-25", 800000), ("2026-05-27", 850000)),
                "QUSCAMUSDA": series(("2025-01-01", 72000), ("2026-01-01", 76000)),
                "Q5ACAMUSDA": series(("2023-01-01", 230000), ("2025-01-01", 260000), ("2026-01-01", 274000)),
                "GFDEBTN": series(("2025-01-01", 36000), ("2026-01-01", 38200)),
                "A091RC1Q027SBEA": series(("2025-01-01", 950), ("2026-01-01", 1120)),
                "DGS10": series(("2026-05-28", 4.55)),
                "DFII10": series(("2026-05-28", 2.05)),
                "T10YIE": series(("2026-05-28", 2.35)),
                "BAMLH0A0HYM2": series(("2026-05-28", 3.65)),
            }
        }
        market_cache = {
            "schema_version": "market_cache.v0.1",
            "generated_at": "2026-05-29T00:00:00Z",
            "assets": {
                "btc_usd": {"price": 108000, "source": "fixture BTC", "as_of": "2026-05-29T00:00:00Z"},
                "gold_usd": {"price": 3350, "source": "pax-gold proxy", "as_of": "2026-05-29T00:00:00Z"},
            },
            "errors": [],
        }

        original_fred_cache = score_aim_macro.FRED_CACHE
        original_market_cache = score_aim_macro.MARKET_CACHE
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            fred_path = tmp / "fred-cache.json"
            market_path = tmp / "market-cache.json"
            fred_path.write_text(json.dumps(fred_cache), encoding="utf-8")
            market_path.write_text(json.dumps(market_cache), encoding="utf-8")
            score_aim_macro.FRED_CACHE = fred_path
            score_aim_macro.MARKET_CACHE = market_path
            try:
                cache = score_aim_macro.build_cache(as_of)
            finally:
                score_aim_macro.FRED_CACHE = original_fred_cache
                score_aim_macro.MARKET_CACHE = original_market_cache

        signal_names = {signal["name"] for signal in cache["signals"]}
        self.assertEqual(cache["freshness"], "local_cache")
        self.assertIn("World Credit Growth", signal_names)
        self.assertIn("U.S. Credit Growth", signal_names)
        self.assertIn("Federal Interest Payments Growth", signal_names)
        self.assertIn("BTC Power Law Fair Value Gap", signal_names)
        self.assertGreater(cache["scores"]["hard_money_repricing"]["score"], 0)

        monetary_signals = [s for s in cache["signals"] if s.get("regime") == "monetary_reset"]
        world_credit = next(s for s in monetary_signals if s["name"] == "World Credit Growth")
        m2 = next(s for s in monetary_signals if s["name"] == "U.S. M2 Liquidity Context")
        self.assertGreater(world_credit["weight"], m2["weight"])
        self.assertIn("3Y annualized", world_credit["value_label"])
        self.assertIn("core monetary reset signal", world_credit["note"])

    def test_dashboard_signal_ledger_prioritizes_small_watchlist(self):
        cache = {
            "signals": [
                {"name": "U.S. M2 Liquidity Context", "regime": "monetary_reset", "weight": 0.03, "score": 50},
                {"name": "World Credit Growth", "regime": "monetary_reset", "weight": 0.28, "score": 65},
                {"name": "BTC Power Law Fair Value Gap", "regime": "hard_money_repricing", "weight": 0.45, "score": 60},
                {"name": "BTC/Gold Ratio", "regime": "hard_money_repricing", "weight": 0.35, "score": 62},
                {"name": "AI Productivity Starter", "regime": "ai_productivity", "weight": 0.35, "score": 55},
                {"name": "AI Capex Bubble Starter", "regime": "ai_bubble_risk", "weight": 0.35, "score": 50},
                {"name": "Federal Interest Payments Growth", "regime": "monetary_reset", "weight": 0.12, "score": 70},
            ]
        }

        names = [signal["name"] for signal in score_aim_macro.dashboard_signal_watchlist(cache)]

        self.assertEqual(
            names,
            [
                "AI Productivity Starter",
                "AI Capex Bubble Starter",
                "BTC Power Law Fair Value Gap",
                "BTC/Gold Ratio",
                "World Credit Growth",
            ],
        )
        self.assertNotIn("U.S. M2 Liquidity Context", names)

    def test_future_dated_market_cache_is_not_counted_fresh(self):
        as_of = date(2020, 1, 1)
        fred_cache = {
            "series": {
                "WM2NS": series(("2019-12-30", 15000)),
                "WALCL": series(("2019-12-25", 4200000)),
                "RRPONTSYD": series(("2019-12-31", 200)),
                "WTREGEN": series(("2019-12-25", 400000)),
                "QUSCAMUSDA": series(("2019-10-01", 52000)),
                "Q5ACAMUSDA": series(("2019-10-01", 210000)),
                "GFDEBTN": series(("2019-10-01", 23000)),
                "A091RC1Q027SBEA": series(("2019-10-01", 600)),
                "DGS10": series(("2019-12-31", 1.92)),
                "DFII10": series(("2019-12-31", 0.10)),
                "T10YIE": series(("2019-12-31", 1.82)),
                "BAMLH0A0HYM2": series(("2019-12-31", 3.60)),
            }
        }
        future_market_cache = {
            "schema_version": "market_cache.v0.1",
            "generated_at": "2026-05-29T00:00:00Z",
            "assets": {
                "btc_usd": {"price": 108000, "source": "future fixture BTC", "as_of": "2026-05-29T00:00:00Z"},
                "gold_usd": {"price": 3350, "source": "future fixture gold", "as_of": "2026-05-29T00:00:00Z"},
            },
            "errors": [],
        }

        original_fred_cache = score_aim_macro.FRED_CACHE
        original_market_cache = score_aim_macro.MARKET_CACHE
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            fred_path = tmp / "fred-cache.json"
            market_path = tmp / "market-cache.json"
            fred_path.write_text(json.dumps(fred_cache), encoding="utf-8")
            market_path.write_text(json.dumps(future_market_cache), encoding="utf-8")
            score_aim_macro.FRED_CACHE = fred_path
            score_aim_macro.MARKET_CACHE = market_path
            try:
                cache = score_aim_macro.build_cache(as_of)
            finally:
                score_aim_macro.FRED_CACHE = original_fred_cache
                score_aim_macro.MARKET_CACHE = original_market_cache

        hard_money_signals = [s for s in cache["signals"] if s.get("regime") == "hard_money_repricing"]
        self.assertTrue(any(signal.get("freshness") == "future" for signal in hard_money_signals))
        self.assertEqual(cache["freshness"], "stale")
        self.assertEqual(cache["scores"]["hard_money_repricing"]["score"], 50)
        self.assertEqual(cache["scores"]["hard_money_repricing"]["confidence"], "low")
    def test_mixed_future_market_inputs_do_not_make_ratio_fresh(self):
        as_of = date(2020, 1, 1)
        market_cache = {
            "assets": {
                "btc_usd": {"price": 108000, "source": "future BTC", "as_of": "2026-05-29T00:00:00Z"},
                "gold_usd": {"price": 1500, "source": "valid gold", "as_of": "2019-12-31T00:00:00Z"},
            }
        }

        signals = score_aim_macro.build_hard_money_signals(market_cache, as_of)
        ratio_signal = next(signal for signal in signals if signal["name"] == "BTC/Gold Ratio")
        self.assertEqual(ratio_signal["freshness"], "future")
        weighted_names = {signal["name"] for signal in score_aim_macro.weighted_hard_money_signals(signals)}
        self.assertNotIn("BTC/Gold Ratio", weighted_names)
        self.assertNotIn("BTC Power Law Fair Value Gap", weighted_names)
        self.assertIn("Gold Proxy Price", weighted_names)


if __name__ == "__main__":
    unittest.main()
