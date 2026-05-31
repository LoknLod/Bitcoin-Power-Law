import re
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

    def test_macro_page_uses_world_credit_not_m2_as_monetary_reset_headline(self):
        html = (ROOT / "macro.html").read_text()
        self.assertIn("World Credit Growth", html)
        self.assertIn("Q5ACAMUSDA", html)
        self.assertIn("3Y Annualized", html)
        self.assertIn("Monetary Reset Subset", html)
        self.assertNotIn("Global M2 Money Supply (US Proxy)", html)

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
