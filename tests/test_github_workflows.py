import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "aim-cache-refresh.yml"
PAGES_WORKFLOW = ROOT / ".github" / "workflows" / "deploy-pages.yml"


class AimCacheRefreshWorkflowTests(unittest.TestCase):
    def test_scheduled_workflow_runs_full_pipeline_and_commits_public_caches_only(self):
        text = WORKFLOW.read_text()

        self.assertIn("schedule:", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("contents: write", text)
        self.assertIn("scripts/run_aim_cache_pipeline.py", text)
        self.assertIn("--include-ai", text)
        self.assertIn("--include-sec", text)
        self.assertIn("--include-filing-text", text)
        self.assertIn("--include-energy", text)
        self.assertIn("SEC_EDGAR_USER_AGENT: ${{ secrets.SEC_EDGAR_USER_AGENT }}", text)
        self.assertIn("ALPHA_VANTAGE_STOCK_API: ${{ secrets.ALPHA_VANTAGE_STOCK_API }}", text)
        self.assertNotIn("FRED_API_KEY: ${{ secrets.FRED_API_KEY }}", text)
        self.assertIn("EIA_API_KEY: ${{ secrets.EIA_API_KEY }}", text)
        self.assertIn("git add aim-cache.json fred-cache.json market-cache.json", text)
        self.assertIn("Guard private portfolio overlay from publication", text)
        self.assertIn("[ -e portfolio-cockpit-cache.json ]", text)
        self.assertIn("git ls-files --error-unmatch portfolio-cockpit-cache.json", text)
        self.assertNotIn("git add .", text)
        self.assertNotIn("git add -A", text)
        self.assertNotRegex(text, r"alpha-vantage-cache\.json|ai-signals-cache\.json|sec-edgar-cache\.json|energy-cache\.json|aim-pipeline-report\.json")

    def test_workflow_contains_no_literal_contact_or_secret_values(self):
        text = WORKFLOW.read_text()

        self.assertNotIn("private-contact@example.invalid", text)
        self.assertNotRegex(text, re.compile(r"(?i)(api[_-]?key\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{8,}|bearer\s+[A-Za-z0-9_.\-]+)"))

    def test_pages_deploy_workflow_uses_configurable_actions_deploy(self):
        text = PAGES_WORKFLOW.read_text()

        self.assertIn("push:", text)
        self.assertIn("branches: [main]", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("pages: write", text)
        self.assertIn("id-token: write", text)
        self.assertIn("group: pages", text)
        self.assertIn("cancel-in-progress: true", text)
        self.assertIn("actions/jekyll-build-pages@v1", text)
        self.assertIn("actions/upload-pages-artifact@v4", text)
        self.assertIn("actions/deploy-pages@v5", text)
        self.assertIn("timeout: 1800000", text)
        self.assertIn("url: ${{ steps.deployment.outputs.page_url }}", text)


if __name__ == "__main__":
    unittest.main()
