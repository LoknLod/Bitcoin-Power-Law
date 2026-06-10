"""Contract tests for the Scriptable widget and its host-side build scripts.

The Shrike host scripts (serve_btc_power_law_private.sh and
shrike_portfolio_cockpit_refresh.sh in ~/.hermes/scripts) generate
widget-private.js by exact-string replacement against widget.js. If those
anchor lines drift, the 08:00/16:00 cockpit refresh cron fails at runtime.
These tests move that failure into CI/dev time.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIDGET = (ROOT / "widget.js").read_text(encoding="utf-8")

# Must stay byte-identical to the `replacements` keys in both host scripts.
SED_ANCHORS = [
    "const PRIVATE_DASHBOARD_URL = normalizeBaseUrl(args.widgetParameter || '');",
    "dashboardUrl: portfolio ? PRIVATE_DASHBOARD_URL : PUBLIC_DASHBOARD_URL,",
]


class WidgetBuildAnchorTests(unittest.TestCase):
    def test_private_build_sed_anchors_exist(self):
        for anchor in SED_ANCHORS:
            self.assertIn(
                anchor,
                WIDGET,
                f"widget.js no longer contains the exact line the private-build "
                f"scripts replace: {anchor!r}. Update ~/.hermes/scripts/"
                f"serve_btc_power_law_private.sh and shrike_portfolio_cockpit_refresh.sh "
                f"in the same change or the cockpit refresh cron will fail.",
            )

    def test_offline_fallback_prefers_private_dashboard(self):
        # Review fix F3: the catch-block fallback must not strand a private
        # user on the public dashboard.
        self.assertIn("dashboardUrl: PRIVATE_DASHBOARD_URL || PUBLIC_DASHBOARD_URL,", WIDGET)


class WidgetPowerLawConsistencyTests(unittest.TestCase):
    def test_constants_match_score_aim_macro(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "score_aim_macro", ROOT / "scripts" / "score_aim_macro.py"
        )
        macro = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(macro)

        js_a = float(re.search(r"const PL_A = (-?[\d.]+);", WIDGET).group(1))
        js_b = float(re.search(r"const PL_B = ([\d.]+);", WIDGET).group(1))
        self.assertEqual(js_a, macro.BTC_POWER_LAW_A)
        self.assertEqual(js_b, macro.BTC_POWER_LAW_B)

        js_genesis = re.search(r"new Date\('(\d{4}-\d{2}-\d{2})T", WIDGET).group(1)
        self.assertEqual(js_genesis, macro.BTC_GENESIS.isoformat())


class WidgetSafetyContractTests(unittest.TestCase):
    def test_private_cockpit_cache_is_validated_before_display(self):
        for marker in [
            "schema_version !== 'shrike_portfolio_cockpit.v0.1'",
            "mutation_allowed !== false",
            "privacy_note",
        ]:
            self.assertIn(marker, WIDGET, f"widget no longer validates: {marker}")

    def test_stale_data_labeling_present(self):
        # Review fix F1: footer must reflect data age, not the render clock.
        self.assertIn("STALE_AFTER_MINUTES", WIDGET)
        self.assertIn("dataAgeLabel", WIDGET)
        self.assertNotIn(
            "now.toLocaleTimeString",
            WIDGET,
            "footer regressed to showing render time instead of data age",
        )

    def test_retirement_row_color_follows_readiness(self):
        # Review fix F2: 'red' readiness must not render in green.
        self.assertIn("readinessColor([data.early2030, data.baseline2035])", WIDGET)

    def test_aim_cache_age_labeling_present(self):
        # Approved follow-up fix 2: the AIM posture row must carry display-only
        # age labeling so weekend-stale regime reads do not look current.
        self.assertIn("const AIM_STALE_AFTER_HOURS = 72;", WIDGET)
        self.assertIn("aimGeneratedAt", WIDGET)
        self.assertIn("dataAgeLabel(data.aimGeneratedAt)", WIDGET)

    def test_market_and_aim_stale_thresholds_are_separate_constants(self):
        # Market data refreshes every 30 minutes (90m stale); AIM caches refresh
        # weekdays only (72h stale). They must not share one constant.
        self.assertIn("const STALE_AFTER_MINUTES = 90;", WIDGET)
        self.assertIn("const AIM_STALE_AFTER_HOURS = 72;", WIDGET)


if __name__ == "__main__":
    unittest.main()
