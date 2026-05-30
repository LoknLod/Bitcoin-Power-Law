import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import update_fred_cache  # noqa: E402
import update_alpha_vantage_cache  # noqa: E402
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


class AlphaVantageCacheUpdaterTests(unittest.TestCase):
    def test_collect_api_keys_prioritizes_stock_key_dedupes_and_ignores_typo_alias(self):
        env = {
            "ALPHAVANTAGE_API_KEY": "shared-key",
            "ALPHA_VANTAGE_STOCK_API": "stock-key",
            "ALPHA_DVANTAGE_API": "typo-key-ignored",
        }

        keys = update_alpha_vantage_cache.collect_api_keys(env)

        self.assertEqual(keys, ["stock-key", "shared-key"])

    def test_collect_api_keys_tolerates_empty_environment(self):
        self.assertEqual(update_alpha_vantage_cache.collect_api_keys({}), [])

    def test_parse_function_payloads_use_stable_cache_keys(self):
        self.assertEqual(update_alpha_vantage_cache.cache_key_for_function("OVERVIEW"), "overview")
        self.assertEqual(update_alpha_vantage_cache.cache_key_for_function("INCOME_STATEMENT"), "income_statement")
        self.assertEqual(update_alpha_vantage_cache.cache_key_for_function("BALANCE_SHEET"), "balance_sheet")
        self.assertEqual(update_alpha_vantage_cache.cache_key_for_function("CASH_FLOW"), "cash_flow")
        self.assertEqual(update_alpha_vantage_cache.cache_key_for_function("EARNINGS"), "earnings")

    def test_build_cache_rotates_keys_and_never_stores_secret_values(self):
        calls = []

        def fake_fetch(function, symbol, api_key):
            calls.append((function, symbol, api_key))
            if function == "OVERVIEW":
                return {"Symbol": symbol, "Name": f"{symbol} Corp"}
            if function == "EARNINGS":
                return {"annualEarnings": [{"fiscalDateEnding": "2025-06-30"}], "quarterlyEarnings": []}
            return {"annualReports": [], "quarterlyReports": [{"fiscalDateEnding": "2026-03-31"}]}

        cache, stats = update_alpha_vantage_cache.build_cache(
            tickers=["MSFT", "NVDA"],
            api_keys=["key-one", "key-two"],
            fetcher=fake_fetch,
            sleep_seconds=0,
            generated_at="2026-05-30T00:00:00Z",
        )

        self.assertEqual(stats["updated_tickers"], 2)
        self.assertEqual(cache["schema_version"], "alpha_vantage_cache.v0.1")
        self.assertEqual(sorted(cache["tickers"]), ["MSFT", "NVDA"])
        self.assertEqual(cache["tickers"]["MSFT"]["overview"]["Symbol"], "MSFT")
        self.assertEqual(cache["tickers"]["MSFT"]["income_statement"]["quarterlyReports"][0]["fiscalDateEnding"], "2026-03-31")
        used_keys = [call[2] for call in calls]
        self.assertIn("key-one", used_keys)
        self.assertIn("key-two", used_keys)
        self.assertEqual(used_keys[:4], ["key-one", "key-two", "key-one", "key-two"])
        serialized = str(cache)
        self.assertNotIn("key-one", serialized)
        self.assertNotIn("key-two", serialized)

    def test_alpha_vantage_limit_payload_records_function_error(self):
        def fake_fetch(function, symbol, api_key):
            if function == "CASH_FLOW":
                return {"Information": "standard API rate limit reached for SECRETKEY"}
            return {"Symbol": symbol} if function == "OVERVIEW" else {"annualReports": [], "quarterlyReports": []}

        cache, stats = update_alpha_vantage_cache.build_cache(
            tickers=["MSFT"],
            api_keys=["SECRETKEY"],
            fetcher=fake_fetch,
            sleep_seconds=0,
            generated_at="2026-05-30T00:00:00Z",
        )

        self.assertEqual(stats["updated_tickers"], 1)
        self.assertEqual(stats["error_count"], 1)
        self.assertEqual(cache["tickers"]["MSFT"]["cash_flow"], {"annualReports": [], "quarterlyReports": []})
        error_text = " ".join(cache["tickers"]["MSFT"]["errors"])
        self.assertIn("CASH_FLOW", error_text)
        self.assertIn("rate limit", error_text)
        self.assertNotIn("SECRETKEY", error_text)


if __name__ == "__main__":
    unittest.main()
