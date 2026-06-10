"""Privacy guards for publicly committed cache artifacts.

The repo is published via GitHub Pages. Private portfolio data must only ever
exist in the gitignored portfolio-cockpit-cache.json served over Tailscale.
These tests fail if private-shaped fields leak into a committed public cache,
or if the gitignore protection on the private cache is removed.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PUBLIC_CACHES = [
    "aim-cache.json",
    "market-cache.json",
    "market-history-cache.json",
]

# Substrings that indicate Doug's private portfolio/plan data.
FORBIDDEN_MARKERS = [
    "schwab",
    "kubera",
    "pension",
    "privacy_note",
    "retirement_plan",
    "account_count",
    "schwab_total",
    "target_portfolio",
    "mutation_allowed",
]


class PublicCachePrivacyTests(unittest.TestCase):
    def test_private_cockpit_cache_is_gitignored(self):
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(
            "portfolio-cockpit-cache.json",
            gitignore,
            "the private cockpit cache lost its gitignore protection",
        )

    def test_public_caches_contain_no_private_fields(self):
        for name in PUBLIC_CACHES:
            path = ROOT / name
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8").lower()
            for marker in FORBIDDEN_MARKERS:
                self.assertNotIn(
                    marker,
                    text,
                    f"public cache {name} contains private-shaped field {marker!r}",
                )

    def test_public_caches_parse_and_have_no_dollar_amount_keys(self):
        # Defense in depth: no key in a public cache should end in *_total,
        # *_value_display, or pension-style suffixes.
        forbidden_key_suffixes = ("_total", "_total_display", "_annual_display", "_gap_to_target")

        def walk(node, path=""):
            if isinstance(node, dict):
                for key, value in node.items():
                    for suffix in forbidden_key_suffixes:
                        self.assertFalse(
                            str(key).endswith(suffix),
                            f"public cache key {path}.{key} looks like private portfolio data",
                        )
                    walk(value, f"{path}.{key}")
            elif isinstance(node, list):
                for index, item in enumerate(node):
                    walk(item, f"{path}[{index}]")

        for name in PUBLIC_CACHES:
            path = ROOT / name
            if not path.exists():
                continue
            walk(json.loads(path.read_text(encoding="utf-8")), name)


if __name__ == "__main__":
    unittest.main()
