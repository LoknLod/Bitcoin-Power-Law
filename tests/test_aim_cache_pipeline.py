import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_aim_cache_pipeline  # noqa: E402


class AimCachePipelineTests(unittest.TestCase):
    def test_plan_uses_ordered_collect_score_render_steps_with_explicit_ai_opt_in(self):
        plan = run_aim_cache_pipeline.build_plan(
            as_of="2026-05-29",
            include_ai=True,
            include_sec=True,
            include_filing_text=True,
            include_energy=True,
            sec_user_agent="Doug Test doug@example.com",
            offline_market=True,
        )

        labels = [step.label for step in plan]
        self.assertEqual(
            labels,
            [
                "update_fred_cache",
                "update_market_cache",
                "update_market_history_cache",
                "update_alpha_vantage_cache",
                "update_sec_edgar_cache",
                "score_ai_signals",
                "update_eia_cache",
                "score_aim_macro",
            ],
        )
        commands = [" ".join(step.command) for step in plan]
        self.assertIn("--offline", commands[1])
        self.assertIn("--as-of 2026-05-29", commands[1])
        self.assertIn("--offline", commands[2])
        self.assertIn("--as-of 2026-05-29", commands[2])
        self.assertIn("--include-filing-text", commands[4])
        self.assertIn("--user-agent Doug Test doug@example.com", commands[4])
        self.assertIn("--sec-cache sec-edgar-cache.json", commands[5])
        self.assertIn("update_eia_cache.py", commands[6])
        self.assertIn("--energy-cache energy-cache.json", commands[7])
        self.assertIn("--ai-signals-cache ai-signals-cache.json", commands[7])

    def test_pipeline_run_writes_report_with_statuses_and_redacts_secrets(self):
        calls = []

        def fake_runner(command, cwd, timeout):
            calls.append(command)
            output = "ok"
            if "update_alpha_vantage_cache.py" in command:
                output = "used SECRET_ALPHA and Doug Test doug@example.com"
            return run_aim_cache_pipeline.CommandResult(returncode=0, stdout=output, stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "pipeline-report.json"
            status = run_aim_cache_pipeline.run_pipeline(
                as_of="2026-05-29",
                include_ai=True,
                include_sec=True,
                include_filing_text=True,
                include_energy=False,
                sec_user_agent="Doug Test doug@example.com",
                offline_market=True,
                dry_run=False,
                report_path=report_path,
                runner=fake_runner,
                env={"ALPHA_VANTAGE_STOCK_API": "SECRET_ALPHA"},
            )
            report = json.loads(report_path.read_text())

        self.assertEqual(status, 0)
        self.assertEqual(len(calls), 7)
        self.assertEqual(report["status"], "success")
        self.assertEqual(report["as_of"], "2026-05-29")
        self.assertEqual([step["status"] for step in report["steps"]], ["success"] * 7)
        serialized = json.dumps(report)
        self.assertNotIn("SECRET_ALPHA", serialized)
        self.assertNotIn("Doug Test doug@example.com", serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_pipeline_stops_on_failed_required_step_and_marks_skipped(self):
        def fake_runner(command, cwd, timeout):
            command_text = " ".join(command)
            if "update_market_cache.py" in command_text:
                return run_aim_cache_pipeline.CommandResult(returncode=2, stdout="", stderr="live price refused")
            return run_aim_cache_pipeline.CommandResult(returncode=0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "pipeline-report.json"
            status = run_aim_cache_pipeline.run_pipeline(
                as_of="2026-05-29",
                include_ai=True,
                include_sec=True,
                include_filing_text=False,
                include_energy=False,
                sec_user_agent="Doug Test doug@example.com",
                offline_market=False,
                dry_run=False,
                report_path=report_path,
                runner=fake_runner,
                env={},
            )
            report = json.loads(report_path.read_text())

        self.assertEqual(status, 2)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["steps"][1]["status"], "failed")
        self.assertTrue(all(step["status"] == "skipped" for step in report["steps"][2:]))
        self.assertIn("live price refused", report["steps"][1]["stderr"])

    def test_pipeline_timeout_writes_redacted_failure_report_and_skips_remaining_steps(self):
        def timeout_runner(command, cwd, timeout):
            raise subprocess.TimeoutExpired(cmd=command, timeout=timeout, output="partial Doug Test doug@example.com")

        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "pipeline-report.json"
            status = run_aim_cache_pipeline.run_pipeline(
                as_of="2026-05-29",
                include_ai=True,
                include_sec=True,
                include_filing_text=True,
                include_energy=False,
                sec_user_agent="Doug Test doug@example.com",
                offline_market=True,
                dry_run=False,
                report_path=report_path,
                runner=timeout_runner,
                env={},
            )
            report = json.loads(report_path.read_text())

        self.assertEqual(status, 124)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["steps"][0]["status"], "failed")
        self.assertTrue(all(step["status"] == "skipped" for step in report["steps"][1:]))
        serialized = json.dumps(report)
        self.assertNotIn("Doug Test doug@example.com", serialized)
        self.assertIn("timed out", serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_include_filing_text_implies_sec_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "pipeline-report.json"
            status = run_aim_cache_pipeline.run_pipeline(
                as_of="2026-05-29",
                include_ai=False,
                include_sec=False,
                include_filing_text=True,
                include_energy=False,
                sec_user_agent="Doug Test doug@example.com",
                offline_market=True,
                dry_run=True,
                report_path=report_path,
                env={},
            )
            report = json.loads(report_path.read_text())

        self.assertEqual(status, 0)
        labels = [step["label"] for step in report["steps"]]
        self.assertIn("update_sec_edgar_cache", labels)
        self.assertIn("score_ai_signals", labels)
        self.assertTrue(report["options"]["include_ai"])
        self.assertTrue(report["options"]["include_sec"])


if __name__ == "__main__":
    unittest.main()
