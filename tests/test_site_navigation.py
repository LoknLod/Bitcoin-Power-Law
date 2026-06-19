import re
import subprocess
import unittest
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
ACTIVE_PAGES = ["index.html", "power-law.html", "macro.html"]
EXPECTED_NAV = [
    ("Dashboard", "index.html"),
    ("Power Law", "power-law.html"),
    ("Macro", "macro.html"),
]
DEPRECATED_TABS = {"Quick", "vs Gold", "Energy", "AIM"}
REDIRECT_SHIMS = {
    "quick.html": "index.html",
    "aim.html": "index.html",
    "gold.html": "macro.html",
    "energy-metrics.html": "macro.html",
}


def nav_items(html: str):
    nav_match = re.search(r'<div class="nav-links">(.*?)</div>', html, re.S)
    if not nav_match:
        return []
    return [
        (re.sub(r"<.*?>", "", label).strip(), href)
        for href, label in re.findall(r'<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a>', nav_match.group(1), re.S)
    ]


def local_href_targets(html: str):
    for href in re.findall(r'<a\s+[^>]*href="([^"]+)"', html, re.S):
        parsed = urlparse(href)
        if parsed.scheme or parsed.netloc or href.startswith("#") or href.startswith("mailto:"):
            continue
        yield href.split("#", 1)[0]


def static_fetch_targets(html: str):
    for target in re.findall(r'fetch\(["\']([^"\']+)["\']', html):
        parsed = urlparse(target)
        if parsed.scheme or parsed.netloc:
            continue
        yield target.split("?", 1)[0]


class SiteNavigationTests(unittest.TestCase):
    def test_active_pages_expose_only_three_top_level_tabs(self):
        for page in ACTIVE_PAGES:
            with self.subTest(page=page):
                items = nav_items((ROOT / page).read_text())
                self.assertEqual(items, EXPECTED_NAV)
                labels = {label for label, _ in items}
                self.assertTrue(labels.isdisjoint(DEPRECATED_TABS))

    def test_removed_top_level_pages_are_redirect_shims_not_tabs(self):
        for page, target in REDIRECT_SHIMS.items():
            with self.subTest(page=page):
                html = (ROOT / page).read_text()
                self.assertIn(target, html)
                self.assertEqual(nav_items(html), [])

    def test_local_links_and_static_fetch_paths_resolve(self):
        pages = list(ROOT.glob("*.html")) + list((ROOT / "archive").glob("*.html"))
        for page in pages:
            html = page.read_text()
            for target in list(local_href_targets(html)) + list(static_fetch_targets(html)):
                if target == "portfolio-cockpit-cache.json":
                    continue
                with self.subTest(page=page.relative_to(ROOT), target=target):
                    resolved = (page.parent / target).resolve()
                    self.assertTrue(
                        resolved.exists(),
                        f"{page.relative_to(ROOT)} references missing local target {target}",
                    )

    def test_widget_points_to_dashboard_not_removed_quick_page(self):
        widget = (ROOT / "widget.js").read_text()
        self.assertNotIn("/quick.html", widget)
        self.assertIn("Bitcoin-Power-Law/", widget)

    def test_widget_uses_private_tailscale_cockpit_with_public_fallback(self):
        widget = (ROOT / "widget.js").read_text()
        self.assertIn("PRIVATE_DASHBOARD_URL", widget)
        self.assertIn("args.widgetParameter", widget)
        private_tailnet_ip_sentinel = ".".join(["100", "122", "204", "74"])
        self.assertNotIn(private_tailnet_ip_sentinel, widget)
        self.assertIn("PUBLIC_DASHBOARD_URL", widget)
        self.assertIn("market-cache.json", widget)
        self.assertIn("portfolio-cockpit-cache.json", widget)
        self.assertIn("Power Law", widget)
        self.assertIn("Conservative Fair", widget)
        self.assertIn("2030", widget)
        self.assertNotIn("mempool.space", widget)
        self.assertNotIn("Hash", widget)

    def test_macro_page_uses_world_credit_not_m2_as_monetary_reset_headline(self):
        html = (ROOT / "macro.html").read_text()
        self.assertIn("World Credit Growth", html)
        self.assertIn("Q5ACAMUSDA", html)
        self.assertIn("3Y Annualized", html)
        self.assertIn("Monetary Reset", html)
        self.assertNotIn("Global M2 Money Supply (US Proxy)", html)

    def test_power_law_page_labels_raw_anchor_and_conservative_fair(self):
        html = (ROOT / "power-law.html").read_text()
        self.assertIn("CONSERVATIVE_FAIR_MULT = 0.71", html)
        self.assertIn("TREND_MULT = 1.0", html)
        self.assertIn("Conservative Fair", html)
        self.assertIn("Trend Anchor", html)
        self.assertIn("market-cache.json", html)
        self.assertIn("AIM scoring uses the raw trend anchor", html)

    def test_macro_page_does_not_duplicate_power_law_position_card(self):
        html = (ROOT / "macro.html").read_text()
        self.assertNotIn("Power Law Position", html)
        self.assertNotIn('id="fair-value"', html)
        self.assertNotIn('id="fair-diff"', html)
        self.assertNotIn('id="pl-marker"', html)

    def test_active_pages_include_inline_favicon_to_avoid_console_noise(self):
        for page in ACTIVE_PAGES:
            with self.subTest(page=page):
                html = (ROOT / page).read_text()
                self.assertIn('<link rel="icon" href="data:,">', html)

    def test_macro_page_uses_local_caches_not_browser_live_market_fetches(self):
        html = (ROOT / "macro.html").read_text()
        self.assertIn("market-cache.json", html)
        self.assertIn("market-history-cache.json", html)
        self.assertIn("fred-cache.json", html)
        self.assertNotIn("raw.githubusercontent.com", html)
        self.assertNotIn("api.coingecko.com", html)
        self.assertNotIn("api.coinbase.com", html)
        self.assertNotIn("if (!btcPrice) btcPrice = 68000", html)
        self.assertNotIn("goldPrice = 2950", html)

    def test_dashboard_supports_private_portfolio_overlay_without_embedding_values(self):
        html = (ROOT / "index.html").read_text()
        self.assertIn("portfolio-cockpit-cache.json", html)
        self.assertIn("loadPortfolioOverlay", html)
        self.assertIn("cache: \"no-store\"", html)
        self.assertIn("schema_version !== \"shrike_portfolio_cockpit.v0.1\"", html)
        self.assertIn("mutation_allowed !== false", html)
        self.assertIn("source !== \"kubera_schwab_plus_aim_cache\"", html)
        self.assertIn("portfolio?.scope !== \"schwab_employer_sponsored_visible_accounts_only\"", html)
        self.assertIn("!data.privacy_note", html)
        self.assertIn("!Number.isFinite(Number(data.portfolio?.schwab_total))", html)
        self.assertIn("catch (error)", html)
        self.assertIn("return null", html)
        self.assertIn("Private portfolio overlay unavailable", html)
        self.assertIn("Retirement Cockpit", html)
        self.assertIn("Schwab Retirement", html)
        self.assertIn("BTC Stack", html)
        self.assertIn("actualAllocationMap", html)
        self.assertIn("allocation-actual-fill", html)
        self.assertIn("Unclassified / Legacy Holdings", html)
        self.assertIn("2030", html)
        self.assertIn("2035", html)
        stale_private_sentinel = "108" + "0023"
        self.assertNotIn(stale_private_sentinel, html)
        self.assertNotIn("Kubera netWorth", html)

    def test_tracked_files_do_not_embed_private_account_identifiers(self):
        tracked = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
        self.assertNotIn("portfolio-cockpit-cache.json", tracked)
        forbidden_export_key = "kubera" + "_netWorth"
        private_patterns = [
            re.compile(r"PCRA Trust - \d{4}"),
            re.compile("whole" + r"_portfolio_total"),
            re.compile(re.escape(forbidden_export_key)),
        ]
        for relative_path in tracked:
            path = ROOT / relative_path
            if path.suffix.lower() not in {".html", ".js", ".py", ".json", ".yml", ".yaml", ".md"}:
                continue
            text = path.read_text(errors="ignore")
            for pattern in private_patterns:
                with self.subTest(path=relative_path, pattern=pattern.pattern):
                    self.assertIsNone(pattern.search(text))

    def test_active_pages_do_not_embed_api_keys(self):
        key_patterns = [
            re.compile(r"[A-Za-z0-9]{32}"),
            re.compile(r"API_KEY\s*=\s*['\"]"),
        ]
        for page in ACTIVE_PAGES:
            html = (ROOT / page).read_text()
            with self.subTest(page=page):
                self.assertNotIn("PASTE YOUR FRED API KEY", html)
                for pattern in key_patterns:
                    self.assertIsNone(pattern.search(html))


if __name__ == "__main__":
    unittest.main()
