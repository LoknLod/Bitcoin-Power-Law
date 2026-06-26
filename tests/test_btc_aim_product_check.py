from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "btc_aim_product_check", ROOT / "scripts" / "btc_aim_product_check.py"
)
assert SPEC is not None
assert SPEC.loader is not None
product_check = importlib.util.module_from_spec(SPEC)
import sys

sys.modules[SPEC.name] = product_check
SPEC.loader.exec_module(product_check)


class BtcAimProductCheckTests(unittest.TestCase):
    def test_default_ok_path_is_quiet_when_no_critical_findings(self):
        with mock.patch.object(product_check, "build_report") as build:
            build.return_value = product_check.Report(status="ok")
            with mock.patch("sys.stdout") as stdout:
                rc = product_check.main([])
        self.assertEqual(rc, 0)
        stdout.write.assert_not_called()

    def test_default_warning_path_is_quiet_and_successful(self):
        report = product_check.Report(status="ok")
        report.add("warning", "market_freshness", "Market cache stale")
        with mock.patch.object(product_check, "build_report", return_value=report):
            with mock.patch("builtins.print") as printer:
                rc = product_check.main([])
        self.assertEqual(rc, 0)
        printer.assert_not_called()

    def test_critical_finding_prints_and_fails(self):
        report = product_check.Report(status="ok")
        report.add("critical", "public_privacy", "Public cache leaked private marker", "schwab_total")
        with mock.patch.object(product_check, "build_report", return_value=report):
            with mock.patch("builtins.print") as printer:
                rc = product_check.main([])
        self.assertEqual(rc, 1)
        rendered = "\n".join(str(call.args[0]) for call in printer.call_args_list)
        self.assertIn("CRITICAL", rendered)
        self.assertIn("public_privacy", rendered)

    def test_review_mode_writes_json_and_markdown_without_self_send(self):
        report = product_check.Report(status="ok")
        report.metrics["market_freshness"] = {"age": "12m", "threshold": "90m"}
        report.metrics["regime_freshness"] = {"age": "2d", "freshness": "local_cache", "stale_signal_count": 0}
        with tempfile.TemporaryDirectory() as tmp:
            product_check.write_review(report, Path(tmp))
            latest_json = json.loads((Path(tmp) / "latest.json").read_text())
            latest_md = (Path(tmp) / "latest.md").read_text()
        self.assertEqual(latest_json["schema_version"], "btc_aim_product_check.v0.1")
        self.assertIn("Approval wall", latest_md)
        self.assertNotIn("send_message", latest_md)
        self.assertNotIn("curl ", latest_md)

    def test_public_privacy_scan_flags_sensitive_cache_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in product_check.PUBLIC_CACHE_FILES:
                (root / name).write_text(json.dumps({"generated_at": "2026-06-26T00:00:00Z"}))
            (root / "aim-cache.json").write_text('{"generated_at":"2026-06-26T00:00:00Z","schwab_total":123}')
            with mock.patch.object(product_check, "ROOT", root):
                report = product_check.Report()
                product_check.check_json_caches(report, product_check.datetime(2026, 6, 26, tzinfo=product_check.timezone.utc))
        self.assertEqual(report.status, "critical")
        self.assertTrue(any(f.check == "public_privacy" for f in report.findings))

    def test_navigation_guard_enforces_three_tabs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nav = '<div class="nav-links"><a href="index.html">Dashboard</a><a href="power-law.html">Power Law</a><a href="macro.html">Macro</a></div>'
            for page in product_check.ACTIVE_PAGES:
                (root / page).write_text(nav)
            for shim in product_check.REDIRECT_SHIMS:
                (root / shim).write_text("redirect")
            with mock.patch.object(product_check, "ROOT", root):
                report = product_check.Report()
                product_check.check_navigation(report)
        self.assertEqual(report.status, "ok")


if __name__ == "__main__":
    unittest.main()
