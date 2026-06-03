import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_portfolio_cockpit_cache  # noqa: E402


SYNTHETIC_PLAN = {
    "btc_held": 1.23,
    "btc_target": 4.56,
    "retirement_target": 1_234_567,
    "pension_annual": 12_345,
}


def build_with_synthetic_plan(portfolio_state, aim_cache=None):
    return build_portfolio_cockpit_cache.build_cache(
        portfolio_state,
        aim_cache or {},
        as_of=date(2026, 6, 3),
        **SYNTHETIC_PLAN,
    )


class PortfolioCockpitCacheTests(unittest.TestCase):
    def test_builds_private_cockpit_cache_from_schwab_portfolio_and_aim_cache(self):
        portfolio_state = {
            "schema_version": "portfolio_state.v0.1",
            "generated_at": "2026-06-01T15:38:40.163426+00:00",
            "source": "kubera",
            "freshness": "live",
            "scope": "schwab_employer_sponsored_visible_accounts_only",
            "mutation_allowed": False,
            "total_value": 400000.0,
            "accounts": [
                {"name": "Synthetic Schwab Account A", "provider": "Charles Schwab", "value": 100000.0, "holdings_count": 2},
                {"name": "Synthetic Schwab Account B", "provider": "Charles Schwab", "value": 300000.0, "holdings_count": 18},
            ],
            "source_diagnostics": {
                "whole" + "_portfolio_total": 999999.99,
                "visible_schwab_account_count": 2,
            },
        }
        aim_cache = {
            "schema_version": "aim_macro_cache.v0.1",
            "generated_at": "2026-06-03T00:00:00Z",
            "posture": {"label": "Barbell", "explanation": "AIM balance."},
            "scores": {"hard_money_repricing": {"score": 63}},
            "signals": [
                {
                    "name": "BTC Power Law Fair Value Gap",
                    "regime": "hard_money_repricing",
                    "score": 62,
                    "value": 0.18,
                    "value_label": "BTC spot is 18.0% below fair value",
                    "as_of": "2026-06-03",
                    "freshness": "local_cache",
                }
            ],
        }

        cache = build_with_synthetic_plan(portfolio_state, aim_cache)

        self.assertEqual(cache["schema_version"], "shrike_portfolio_cockpit.v0.1")
        self.assertFalse(cache["mutation_allowed"])
        self.assertEqual(cache["source"], "kubera_schwab_plus_aim_cache")
        self.assertEqual(cache["portfolio"]["scope"], "schwab_employer_sponsored_visible_accounts_only")
        self.assertEqual(cache["portfolio"]["schwab_total"], 400000.0)
        self.assertEqual(cache["portfolio"]["account_count"], 2)
        self.assertNotIn("accounts", cache["portfolio"])
        self.assertEqual(cache["btc"]["held"], SYNTHETIC_PLAN["btc_held"])
        self.assertEqual(cache["btc"]["target"], SYNTHETIC_PLAN["btc_target"])
        self.assertEqual(cache["btc"]["target_gap"], 3.33)
        self.assertEqual(cache["btc"]["provenance"], "private_plan")
        self.assertEqual(cache["retirement"]["target_portfolio"], SYNTHETIC_PLAN["retirement_target"])
        self.assertEqual(cache["retirement"]["schwab_gap_to_target"], 834567.0)
        self.assertEqual(cache["retirement"]["pension_annual"], SYNTHETIC_PLAN["pension_annual"])
        self.assertGreater(cache["retirement"]["required_annual_growth_to_2030"], 0)
        self.assertIn("private local overlay", cache["privacy_note"])
        serialized = json.dumps(cache)
        self.assertNotIn("Synthetic Schwab Account", serialized)
        self.assertNotIn("999999", serialized)


    def test_builds_actual_aim5_allocation_from_private_kubera_raw(self):
        raw_detail = {
            "data": {
                "asset": [
                    {"id": "acct-a", "hidden": 0, "parent": None, "connection": {"providerName": "Charles Schwab"}, "type": "investment", "value": {"amount": 300000}},
                    {"ticker": "NVDA", "parent": {"id": "acct-a"}, "value": {"amount": 120000}},
                    {"ticker": "IBIT", "parent": {"id": "acct-a"}, "value": {"amount": 60000}},
                    {"ticker": "FAKELEGACY", "parent": {"id": "acct-a"}, "value": {"amount": 120000}},
                    {"ticker": "MSFT", "parent": {"id": "other"}, "value": {"amount": 999999}},
                ]
            }
        }
        config = {
            "candidate_tickers": {
                "ai_productive_equity": ["NVDA"],
                "hard_money": ["IBIT"],
            }
        }

        actual = build_portfolio_cockpit_cache.build_actual_allocation(raw_detail, config)

        self.assertTrue(actual["available"])
        self.assertEqual(actual["scope"], "schwab_employer_sponsored_visible_accounts_only")
        by_key = {item["sleeve_key"]: item for item in actual["sleeves"]}
        self.assertEqual(by_key["ai_productive_equity"]["actual_pct"], 40.0)
        self.assertEqual(by_key["hard_money"]["actual_pct"], 20.0)
        self.assertEqual(by_key["unclassified"]["actual_pct"], 40.0)
        self.assertNotIn("FAKELEGACY", json.dumps(actual))
        self.assertNotIn("actual_value", json.dumps(actual))

    def test_rejects_mutable_or_wrong_scope_portfolio_state(self):
        bad = {
            "schema_version": "portfolio_state.v0.1",
            "source": "kubera",
            "freshness": "live",
            "scope": "whole_kubera_networth",
            "mutation_allowed": True,
            "total_value": 123,
            "accounts": [],
        }

        with self.assertRaises(ValueError):
            build_with_synthetic_plan(bad)

    def test_recomputes_schwab_total_from_account_rows_and_rejects_mismatch(self):
        state = {
            "schema_version": "portfolio_state.v0.1",
            "source": "kubera",
            "freshness": "live",
            "scope": "schwab_employer_sponsored_visible_accounts_only",
            "mutation_allowed": False,
            "total_value": 500000.0,
            "accounts": [
                {"name": "Synthetic", "provider": "Charles Schwab", "value": 100000.0},
                {"name": "Synthetic", "provider": "Charles Schwab", "value": 300000.0},
            ],
        }

        with self.assertRaises(ValueError):
            build_with_synthetic_plan(state)

    def test_rejects_missing_account_rows_instead_of_trusting_total_value_alone(self):
        state = {
            "schema_version": "portfolio_state.v0.1",
            "source": "kubera",
            "freshness": "live",
            "scope": "schwab_employer_sponsored_visible_accounts_only",
            "mutation_allowed": False,
            "total_value": 400000.0,
            "accounts": [],
        }

        with self.assertRaises(ValueError):
            build_with_synthetic_plan(state)

    def test_command_requires_private_plan_values_without_public_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            portfolio_path = tmp / "portfolio_state.json"
            aim_path = tmp / "aim-cache.json"
            private_plan_path = tmp / "missing-private-plan.json"
            output_path = tmp / "portfolio-cockpit-cache.json"
            portfolio_path.write_text(json.dumps({
                "schema_version": "portfolio_state.v0.1",
                "source": "kubera",
                "freshness": "live",
                "scope": "schwab_employer_sponsored_visible_accounts_only",
                "mutation_allowed": False,
                "total_value": 400000.0,
                "accounts": [{"provider": "Charles Schwab", "value": 400000.0}],
            }), encoding="utf-8")
            aim_path.write_text(json.dumps({"schema_version": "aim_macro_cache.v0.1", "signals": []}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Missing private plan value"):
                build_portfolio_cockpit_cache.run([
                    "--portfolio-state", str(portfolio_path),
                    "--aim-cache", str(aim_path),
                    "--private-plan", str(private_plan_path),
                    "--output", str(output_path),
                    "--as-of", "2026-06-03",
                ])

    def test_command_writes_private_cache_without_printing_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            portfolio_path = tmp / "portfolio_state.json"
            aim_path = tmp / "aim-cache.json"
            private_plan_path = tmp / "retirement_plan_private.json"
            output_path = tmp / "portfolio-cockpit-cache.json"
            portfolio_path.write_text(json.dumps({
                "schema_version": "portfolio_state.v0.1",
                "generated_at": "2026-06-01T00:00:00Z",
                "source": "kubera",
                "freshness": "live",
                "scope": "schwab_employer_sponsored_visible_accounts_only",
                "mutation_allowed": False,
                "total_value": 400000.0,
                "accounts": [{"provider": "Charles Schwab", "value": 400000.0}],
            }), encoding="utf-8")
            aim_path.write_text(json.dumps({"schema_version": "aim_macro_cache.v0.1", "signals": []}), encoding="utf-8")
            private_plan_path.write_text(json.dumps({
                "schema_version": "retirement_plan_private.v0.1",
                "mutation_allowed": False,
                **SYNTHETIC_PLAN,
            }), encoding="utf-8")

            message = build_portfolio_cockpit_cache.run(
                [
                    "--portfolio-state", str(portfolio_path),
                    "--aim-cache", str(aim_path),
                    "--private-plan", str(private_plan_path),
                    "--output", str(output_path),
                    "--as-of", "2026-06-03",
                ]
            )

            self.assertTrue(output_path.exists())
            self.assertIn("Wrote portfolio-cockpit-cache.json", message)
            self.assertNotIn("400000", message)
            self.assertNotIn(str(SYNTHETIC_PLAN["retirement_target"]), message)


if __name__ == "__main__":
    unittest.main()
