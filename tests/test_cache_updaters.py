import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import update_fred_cache  # noqa: E402
import update_market_cache  # noqa: E402


class FredCacheUpdaterTests(unittest.TestCase):
    def test_fred_csv_parser_ignores_missing_dot_values(self):
        csv_text = "\n".join(
            [
                "observation_date,TEST",
                "2026-01-01,1.25",
                "2026-01-02,.",
                "2026-01-03,2",
            ]
        )

        observations = update_fred_cache.parse_fred_csv("TEST", csv_text)

        self.assertEqual(
            observations,
            [
                {"date": "2026-01-01", "value": "1.25"},
                {"date": "2026-01-03", "value": "2"},
            ],
        )


class MarketCacheUpdaterTests(unittest.TestCase):
    def test_coingecko_and_coinbase_payload_normalization(self):
        generated_at = "2026-05-29T00:00:00Z"
        payload = {
            "bitcoin": {"usd": 108000.5, "last_updated_at": 1780012800},
            "pax-gold": {"usd": "3350.25", "last_updated_at": 1780012800},
        }

        assets, errors = update_market_cache.parse_coingecko_simple_price(payload, generated_at)

        self.assertEqual(errors, [])
        self.assertEqual(assets["btc_usd"]["price"], 108000.5)
        self.assertEqual(assets["gold_usd"]["price"], 3350.25)
        self.assertEqual(assets["gold_usd"]["source"], "pax-gold proxy")
        self.assertEqual(update_market_cache.parse_coinbase_spot({"data": {"amount": "109001.75"}}), 109001.75)


if __name__ == "__main__":
    unittest.main()
