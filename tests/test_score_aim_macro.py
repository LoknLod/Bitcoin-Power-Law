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
        self.assertEqual(cache["aim_schema_version"], score_aim_macro.SCHEMA_VERSION)
        self.assertEqual(cache["aim_scoring_version"], score_aim_macro.SCORING_VERSION)
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
        self.assertIn("148 days old", world_credit["staleness_warning"])
        self.assertEqual(cache["scores"]["monetary_reset"]["confidence"], "low")

    def test_dashboard_signal_ledger_prioritizes_small_watchlist(self):
        cache = {
            "signals": [
                {"name": "U.S. M2 Liquidity Context", "regime": "monetary_reset", "weight": 0.03, "score": 50},
                {"name": "World Credit Growth", "regime": "monetary_reset", "weight": 0.28, "score": 65},
                {"name": "BTC Power Law Fair Value Gap", "regime": "hard_money_repricing", "weight": 0.45, "score": 60},
                {"name": "BTC/Gold Ratio", "regime": "hard_money_repricing", "weight": 0.35, "score": 62},
                {"name": "AI Productivity Score", "regime": "ai_productivity", "weight": 0.35, "score": 64},
                {"name": "AI Capex Bubble Risk", "regime": "ai_bubble_risk", "weight": 0.35, "score": 71},
                {"name": "AI Productivity Starter", "regime": "ai_productivity", "weight": 0.35, "score": 55},
                {"name": "AI Capex Bubble Starter", "regime": "ai_bubble_risk", "weight": 0.35, "score": 50},
                {"name": "Federal Interest Payments Growth", "regime": "monetary_reset", "weight": 0.12, "score": 70},
            ]
        }

        names = [signal["name"] for signal in score_aim_macro.dashboard_signal_watchlist(cache)]

        self.assertEqual(
            names,
            [
                "AI Productivity Score",
                "AI Capex Bubble Risk",
                "BTC Power Law Fair Value Gap",
                "BTC/Gold Ratio",
                "World Credit Growth",
            ],
        )
        self.assertNotIn("U.S. M2 Liquidity Context", names)

    def test_dashboard_watchlist_falls_back_to_starter_ai_signals(self):
        cache = {
            "signals": [
                {"name": "World Credit Growth", "regime": "monetary_reset", "weight": 0.28, "score": 65},
                {"name": "BTC Power Law Fair Value Gap", "regime": "hard_money_repricing", "weight": 0.45, "score": 60},
                {"name": "BTC/Gold Ratio", "regime": "hard_money_repricing", "weight": 0.35, "score": 62},
                {"name": "AI Productivity Starter", "regime": "ai_productivity", "weight": 0.35, "score": 55},
                {"name": "AI Capex Bubble Starter", "regime": "ai_bubble_risk", "weight": 0.35, "score": 50},
            ]
        }

        names = [signal["name"] for signal in score_aim_macro.dashboard_signal_watchlist(cache)]

        self.assertEqual(names[:2], ["AI Productivity Starter", "AI Capex Bubble Starter"])

    def test_build_cache_replaces_starter_ai_signals_with_ai_signal_cache(self):
        as_of = date(2026, 5, 29)
        ai_cache = {
            "schema_version": "ai_signals_cache.v0.1",
            "generated_at": "2026-05-29T00:00:00Z",
            "metadata": {"company_count": 2},
            "signals": {
                "ai_productivity": {
                    "name": "AI Productivity Score",
                    "score": 68.4,
                    "direction": "higher_is_better",
                    "leaders": ["GOOD"],
                    "source": "alpha_vantage_cache",
                    "note": "Revenue and margin proof.",
                },
                "ai_capex_bubble_risk": {
                    "name": "AI Capex Bubble Risk",
                    "score": 72.2,
                    "direction": "higher_is_riskier",
                    "leaders": ["BUBBLE"],
                    "source": "alpha_vantage_cache",
                    "note": "Capex outrunning monetization.",
                },
            },
        }

        original_fred_cache = score_aim_macro.FRED_CACHE
        original_market_cache = score_aim_macro.MARKET_CACHE
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            ai_path = tmp / "ai-signals-cache.json"
            ai_path.write_text(json.dumps(ai_cache), encoding="utf-8")
            score_aim_macro.FRED_CACHE = tmp / "missing-fred-cache.json"
            score_aim_macro.MARKET_CACHE = tmp / "missing-market-cache.json"
            try:
                cache = score_aim_macro.build_cache(as_of, ai_signals_cache_path=ai_path)
            finally:
                score_aim_macro.FRED_CACHE = original_fred_cache
                score_aim_macro.MARKET_CACHE = original_market_cache

        signal_names = [signal["name"] for signal in cache["signals"]]
        dashboard_names = [signal["name"] for signal in cache["dashboard_signals"]]
        self.assertIn("AI Productivity Score", signal_names)
        self.assertIn("AI Capex Bubble Risk", signal_names)
        self.assertNotIn("AI Productivity Starter", signal_names)
        self.assertEqual(dashboard_names[:2], ["AI Productivity Score", "AI Capex Bubble Risk"])
        self.assertEqual(cache["scores"]["ai_productivity"]["score"], 68)
        self.assertEqual(cache["scores"]["ai_bubble_risk"]["score"], 72)
        self.assertEqual(cache["scores"]["ai_productivity"]["confidence"], "medium")

    def test_build_cache_replaces_energy_starter_with_eia_energy_cache(self):
        as_of = date(2026, 5, 29)
        energy_cache = {
            "schema_version": "eia_energy_cache.v0.1",
            "generated_at": "2026-05-29T00:00:00Z",
            "series": {
                "commercial_electricity": {
                    "observations": [
                        {"date": "2025-03", "price_cents_per_kwh": 12.0, "sales_million_kwh": 100000},
                        {"date": "2026-03", "price_cents_per_kwh": 15.0, "sales_million_kwh": 118000},
                    ]
                },
                "industrial_electricity": {
                    "observations": [
                        {"date": "2025-03", "price_cents_per_kwh": 7.5, "sales_million_kwh": 80000},
                        {"date": "2026-03", "price_cents_per_kwh": 8.1, "sales_million_kwh": 82000},
                    ]
                },
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            energy_path = tmp / "energy-cache.json"
            energy_path.write_text(json.dumps(energy_cache), encoding="utf-8")
            cache = score_aim_macro.build_cache(as_of, energy_cache_path=energy_path)

        signal = next(s for s in cache["signals"] if s.get("regime") == "energy_bottleneck")
        self.assertEqual(signal["name"], "Energy Bottleneck Score")
        self.assertEqual(signal["freshness"], "local_cache")
        self.assertEqual(signal["source"], "eia_energy_cache")
        self.assertIn("commercial electricity", signal["note"])
        self.assertIn("+25.0%", signal["value_label"])
        self.assertGreater(cache["scores"]["energy_bottleneck"]["score"], 60)
        self.assertEqual(cache["scores"]["energy_bottleneck"]["confidence"], "medium")

    def test_build_cache_ignores_local_energy_cache_without_explicit_opt_in(self):
        as_of = date(2026, 5, 29)
        energy_cache = {
            "schema_version": "eia_energy_cache.v0.1",
            "series": {
                "commercial_electricity": {
                    "observations": [
                        {"date": "2025-03", "price_cents_per_kwh": 10.0, "sales_million_kwh": 100000},
                        {"date": "2026-03", "price_cents_per_kwh": 99.0, "sales_million_kwh": 200000},
                    ]
                }
            },
        }

        original_energy_cache = score_aim_macro.ENERGY_CACHE
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            score_aim_macro.ENERGY_CACHE = tmp / "energy-cache.json"
            score_aim_macro.ENERGY_CACHE.write_text(json.dumps(energy_cache), encoding="utf-8")
            try:
                cache = score_aim_macro.build_cache(as_of)
            finally:
                score_aim_macro.ENERGY_CACHE = original_energy_cache

        signal = next(s for s in cache["signals"] if s.get("regime") == "energy_bottleneck")
        self.assertEqual(signal["name"], "Energy Bottleneck Starter")
        self.assertTrue(signal.get("placeholder"))
        self.assertEqual(cache["scores"]["energy_bottleneck"]["score"], 60)
        self.assertEqual(cache["scores"]["energy_bottleneck"]["confidence"], "low")

    def test_build_energy_signal_rejects_malformed_stale_and_future_cache(self):
        as_of = date(2026, 5, 29)
        generated_at = "2026-05-29T00:00:00Z"
        malformed = {"schema_version": "eia_energy_cache.v0.1", "series": {}}
        self.assertEqual(score_aim_macro.build_energy_signal(malformed, generated_at, as_of)["name"], "Energy Bottleneck Starter")

        stale = {
            "schema_version": "eia_energy_cache.v0.1",
            "series": {"commercial_electricity": {"observations": [{"date": "2020-03", "price_cents_per_kwh": 12, "sales_million_kwh": 100000}]}}
        }
        self.assertEqual(score_aim_macro.build_energy_signal(stale, generated_at, as_of)["freshness"], "starter")

        future = {
            "schema_version": "eia_energy_cache.v0.1",
            "series": {"commercial_electricity": {"observations": [{"date": "2027-03", "price_cents_per_kwh": 12, "sales_million_kwh": 100000}]}}
        }
        self.assertEqual(score_aim_macro.build_energy_signal(future, generated_at, as_of)["freshness"], "future")

    def test_build_energy_signal_excludes_future_rows_during_historical_replay(self):
        as_of = date(2026, 5, 29)
        generated_at = "2026-05-29T00:00:00Z"
        mixed = {
            "schema_version": "eia_energy_cache.v0.1",
            "series": {
                "commercial_electricity": {
                    "observations": [
                        {"date": "2025-03", "price_cents_per_kwh": 12, "sales_million_kwh": 100000},
                        {"date": "2026-03", "price_cents_per_kwh": 15, "sales_million_kwh": 118000},
                        {"date": "2027-03", "price_cents_per_kwh": 99, "sales_million_kwh": 300000},
                    ]
                }
            },
        }

        signal = score_aim_macro.build_energy_signal(mixed, generated_at, as_of)

        self.assertEqual(signal["freshness"], "local_cache")
        self.assertEqual(signal["as_of"], "2026-03-01")
        self.assertNotIn("+560.0%", signal["value_label"])

    def test_build_energy_signal_ignores_stale_component_series(self):
        as_of = date(2026, 5, 29)
        generated_at = "2026-05-29T00:00:00Z"
        mixed = {
            "schema_version": "eia_energy_cache.v0.1",
            "series": {
                "commercial_electricity": {
                    "observations": [
                        {"date": "2025-03", "price_cents_per_kwh": 12, "sales_million_kwh": 100000},
                        {"date": "2026-03", "price_cents_per_kwh": 13, "sales_million_kwh": 104000},
                    ]
                },
                "industrial_electricity": {
                    "observations": [
                        {"date": "2019-03", "price_cents_per_kwh": 1},
                        {"date": "2020-03", "price_cents_per_kwh": 100},
                    ]
                },
            },
        }

        signal = score_aim_macro.build_energy_signal(mixed, generated_at, as_of)

        self.assertEqual(signal["freshness"], "local_cache")
        self.assertNotIn("industrial electricity price", signal["value_label"])
        self.assertLess(signal["score"], 70)

    def test_build_cache_ignores_local_ai_signal_cache_without_explicit_opt_in(self):
        as_of = date(2026, 5, 29)
        ai_cache = {
            "schema_version": "ai_signals_cache.v0.1",
            "generated_at": "2026-05-29T00:00:00Z",
            "metadata": {"company_count": 1},
            "signals": {
                "ai_productivity": {"name": "AI Productivity Score", "score": 99},
                "ai_capex_bubble_risk": {"name": "AI Capex Bubble Risk", "score": 99},
            },
        }

        original_fred_cache = score_aim_macro.FRED_CACHE
        original_market_cache = score_aim_macro.MARKET_CACHE
        original_ai_cache = score_aim_macro.AI_SIGNALS_CACHE
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            score_aim_macro.FRED_CACHE = tmp / "missing-fred-cache.json"
            score_aim_macro.MARKET_CACHE = tmp / "missing-market-cache.json"
            score_aim_macro.AI_SIGNALS_CACHE = tmp / "ai-signals-cache.json"
            score_aim_macro.AI_SIGNALS_CACHE.write_text(json.dumps(ai_cache), encoding="utf-8")
            try:
                cache = score_aim_macro.build_cache(as_of)
            finally:
                score_aim_macro.FRED_CACHE = original_fred_cache
                score_aim_macro.MARKET_CACHE = original_market_cache
                score_aim_macro.AI_SIGNALS_CACHE = original_ai_cache

        signal_names = [signal["name"] for signal in cache["signals"]]
        self.assertIn("AI Productivity Starter", signal_names)
        self.assertNotIn("AI Productivity Score", signal_names)
        starter_signal = next(signal for signal in cache["signals"] if signal["name"] == "AI Productivity Starter")
        self.assertTrue(starter_signal.get("placeholder"))
        self.assertEqual(cache["scores"]["ai_productivity"]["score"], 55)

    def test_build_ai_signals_rejects_empty_malformed_or_stale_cache(self):
        as_of = date(2026, 5, 29)
        generated_at = "2026-05-29T00:00:00Z"
        base = {
            "schema_version": "ai_signals_cache.v0.1",
            "generated_at": generated_at,
            "metadata": {"company_count": 0},
            "signals": {
                "ai_productivity": {"name": "AI Productivity Score", "score": 99},
                "ai_capex_bubble_risk": {"name": "AI Capex Bubble Risk", "score": 99},
            },
        }
        empty_names = [signal["name"] for signal in score_aim_macro.build_ai_signals(base, generated_at, as_of)]
        self.assertIn("AI Productivity Starter", empty_names)

        malformed = json.loads(json.dumps(base))
        malformed["metadata"]["company_count"] = 1
        malformed["signals"]["ai_productivity"]["score"] = "bad"
        malformed_names = [signal["name"] for signal in score_aim_macro.build_ai_signals(malformed, generated_at, as_of)]
        self.assertIn("AI Productivity Starter", malformed_names)

        stale = json.loads(json.dumps(base))
        stale["metadata"]["company_count"] = 1
        stale["company_metrics"] = [{"ticker": "MSFT", "as_of": "2020-03-31"}]
        stale_names = [signal["name"] for signal in score_aim_macro.build_ai_signals(stale, generated_at, as_of)]
        self.assertIn("AI Productivity Starter", stale_names)

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
