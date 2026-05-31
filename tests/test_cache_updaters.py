import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import update_fred_cache  # noqa: E402
import update_alpha_vantage_cache  # noqa: E402
import update_market_cache  # noqa: E402
import score_ai_signals  # noqa: E402
import update_sec_edgar_cache  # noqa: E402
import update_eia_cache  # noqa: E402


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


class EiaCacheUpdaterTests(unittest.TestCase):
    def test_collect_api_keys_prefers_eia_api_and_dedupes_alias(self):
        env = {"EIA_API": "primary-key", "EIA_API_KEY": "primary-key"}

        keys = update_eia_cache.collect_api_keys(env)

        self.assertEqual(keys, ["primary-key"])

    def test_normalize_electricity_rows_keeps_numeric_observations(self):
        rows = [
            {
                "period": "2026-03",
                "sectorid": "COM",
                "sectorName": "commercial",
                "price": "13.92",
                "sales": "119621.52556",
                "price-units": "cents per kilowatt-hour",
                "sales-units": "million kilowatt hours",
            },
            {"period": "2026-02", "sectorid": "COM", "price": ".", "sales": "bad"},
        ]

        observations = update_eia_cache.normalize_electricity_rows(rows, "commercial_electricity")

        self.assertEqual(
            observations,
            [
                {
                    "date": "2026-03",
                    "price_cents_per_kwh": 13.92,
                    "sales_million_kwh": 119621.52556,
                    "sector_id": "COM",
                    "sector_name": "commercial",
                    "source_series": "commercial_electricity",
                }
            ],
        )

    def test_build_cache_fetches_curated_electricity_series_and_never_stores_key(self):
        calls = []

        def fake_fetch(series, api_key):
            calls.append((series["key"], api_key))
            return {
                "response": {
                    "data": [
                        {
                            "period": "2026-03",
                            "sectorid": series["sector_id"],
                            "sectorName": series["name"],
                            "price": "13.92",
                            "sales": "119621.52556",
                            "price-units": "cents per kilowatt-hour",
                            "sales-units": "million kilowatt hours",
                        }
                    ]
                }
            }

        cache, stats = update_eia_cache.build_cache(
            api_keys=["SECRET_EIA_KEY"],
            fetcher=fake_fetch,
            sleep_seconds=0,
            generated_at="2026-05-31T00:00:00Z",
        )

        self.assertEqual(cache["schema_version"], "eia_energy_cache.v0.1")
        self.assertEqual(cache["source"], "eia_api")
        self.assertEqual(stats["series_count"], len(update_eia_cache.CURATED_SERIES))
        self.assertEqual(stats["updated_series"], len(update_eia_cache.CURATED_SERIES))
        self.assertEqual(cache["series"]["commercial_electricity"]["observations"][0]["price_cents_per_kwh"], 13.92)
        serialized = json.dumps(cache)
        self.assertNotIn("SECRET_EIA_KEY", serialized)

    def test_build_cache_redacts_key_from_errors(self):
        def fake_fetch(series, api_key):
            raise RuntimeError(f"provider rejected {api_key}")

        cache, stats = update_eia_cache.build_cache(
            api_keys=["SECRET_EIA_KEY"],
            fetcher=fake_fetch,
            sleep_seconds=0,
            generated_at="2026-05-31T00:00:00Z",
        )

        self.assertEqual(stats["error_count"], len(update_eia_cache.CURATED_SERIES))
        serialized = json.dumps(cache)
        self.assertNotIn("SECRET_EIA_KEY", serialized)
        self.assertIn("[REDACTED]", serialized)


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


class AISignalScoringTests(unittest.TestCase):
    def test_company_metrics_compute_capex_margin_fcf_and_yoy(self):
        ticker_payload = {
            "income_statement": {"quarterlyReports": [
                {"fiscalDateEnding": "2026-03-31", "totalRevenue": "1000", "operatingIncome": "250", "researchAndDevelopment": "120"},
                {"fiscalDateEnding": "2025-03-31", "totalRevenue": "800", "operatingIncome": "180", "researchAndDevelopment": "80"},
            ]},
            "cash_flow": {"quarterlyReports": [
                {"fiscalDateEnding": "2026-03-31", "operatingCashflow": "300", "capitalExpenditures": "-200", "depreciationDepletionAndAmortization": "60"},
                {"fiscalDateEnding": "2025-03-31", "operatingCashflow": "260", "capitalExpenditures": "-100", "depreciationDepletionAndAmortization": "40"},
            ]},
            "balance_sheet": {"quarterlyReports": [
                {"fiscalDateEnding": "2026-03-31", "shortTermDebt": "50", "longTermDebt": "450", "cashAndCashEquivalentsAtCarryingValue": "200"},
                {"fiscalDateEnding": "2025-03-31", "shortTermDebt": "50", "longTermDebt": "350", "cashAndCashEquivalentsAtCarryingValue": "150"},
            ]},
        }

        metrics = score_ai_signals.company_metrics("MSFT", ticker_payload)

        self.assertEqual(metrics["ticker"], "MSFT")
        self.assertEqual(metrics["as_of"], "2026-03-31")
        self.assertAlmostEqual(metrics["revenue_yoy_pct"], 25.0)
        self.assertAlmostEqual(metrics["capex_yoy_pct"], 100.0)
        self.assertAlmostEqual(metrics["capex_to_revenue_pct"], 20.0)
        self.assertAlmostEqual(metrics["operating_margin_pct"], 25.0)
        self.assertAlmostEqual(metrics["free_cash_flow_margin_pct"], 10.0)
        self.assertAlmostEqual(metrics["debt_yoy_pct"], 25.0)
        self.assertAlmostEqual(metrics["r_and_d_to_revenue_pct"], 12.0)

    def test_build_ai_signal_cache_scores_productivity_and_bubble_risk(self):
        alpha_cache = {
            "schema_version": "alpha_vantage_cache.v0.1",
            "generated_at": "2026-05-30T00:00:00Z",
            "tickers": {
                "GOOD": {
                    "income_statement": {"quarterlyReports": [
                        {"fiscalDateEnding": "2026-03-31", "totalRevenue": "1200", "operatingIncome": "360", "researchAndDevelopment": "120"},
                        {"fiscalDateEnding": "2025-03-31", "totalRevenue": "1000", "operatingIncome": "250", "researchAndDevelopment": "100"},
                    ]},
                    "cash_flow": {"quarterlyReports": [
                        {"fiscalDateEnding": "2026-03-31", "operatingCashflow": "420", "capitalExpenditures": "-120", "depreciationDepletionAndAmortization": "50"},
                        {"fiscalDateEnding": "2025-03-31", "operatingCashflow": "350", "capitalExpenditures": "-100", "depreciationDepletionAndAmortization": "40"},
                    ]},
                    "balance_sheet": {"quarterlyReports": [
                        {"fiscalDateEnding": "2026-03-31", "shortTermDebt": "10", "longTermDebt": "190"},
                        {"fiscalDateEnding": "2025-03-31", "shortTermDebt": "10", "longTermDebt": "190"},
                    ]},
                },
                "BUBBLE": {
                    "income_statement": {"quarterlyReports": [
                        {"fiscalDateEnding": "2026-03-31", "totalRevenue": "1000", "operatingIncome": "80", "researchAndDevelopment": "300"},
                        {"fiscalDateEnding": "2025-03-31", "totalRevenue": "980", "operatingIncome": "100", "researchAndDevelopment": "160"},
                    ]},
                    "cash_flow": {"quarterlyReports": [
                        {"fiscalDateEnding": "2026-03-31", "operatingCashflow": "180", "capitalExpenditures": "-500", "depreciationDepletionAndAmortization": "140"},
                        {"fiscalDateEnding": "2025-03-31", "operatingCashflow": "200", "capitalExpenditures": "-150", "depreciationDepletionAndAmortization": "50"},
                    ]},
                    "balance_sheet": {"quarterlyReports": [
                        {"fiscalDateEnding": "2026-03-31", "shortTermDebt": "200", "longTermDebt": "800"},
                        {"fiscalDateEnding": "2025-03-31", "shortTermDebt": "100", "longTermDebt": "400"},
                    ]},
                },
            },
        }

        cache = score_ai_signals.build_cache(alpha_cache, generated_at="2026-05-30T00:00:00Z")

        self.assertEqual(cache["schema_version"], "ai_signals_cache.v0.1")
        self.assertEqual(cache["metadata"]["company_count"], 2)
        self.assertEqual(cache["signals"]["ai_productivity"]["name"], "AI Productivity Score")
        self.assertGreater(cache["signals"]["ai_productivity"]["score"], 50)
        self.assertEqual(cache["signals"]["ai_capex_bubble_risk"]["name"], "AI Capex Bubble Risk")
        self.assertGreater(cache["signals"]["ai_capex_bubble_risk"]["score"], 50)
        self.assertIn("BUBBLE", cache["signals"]["ai_capex_bubble_risk"]["leaders"])

    def test_build_ai_signal_cache_blends_sec_language_evidence(self):
        alpha_cache = {
            "schema_version": "alpha_vantage_cache.v0.1",
            "generated_at": "2026-05-30T00:00:00Z",
            "tickers": {
                "GOOD": {
                    "income_statement": {"quarterlyReports": [
                        {"fiscalDateEnding": "2026-03-31", "totalRevenue": "1200", "operatingIncome": "360", "researchAndDevelopment": "120"},
                        {"fiscalDateEnding": "2025-03-31", "totalRevenue": "1000", "operatingIncome": "250", "researchAndDevelopment": "100"},
                    ]},
                    "cash_flow": {"quarterlyReports": [
                        {"fiscalDateEnding": "2026-03-31", "operatingCashflow": "420", "capitalExpenditures": "-120"},
                        {"fiscalDateEnding": "2025-03-31", "operatingCashflow": "350", "capitalExpenditures": "-100"},
                    ]},
                    "balance_sheet": {"quarterlyReports": [
                        {"fiscalDateEnding": "2026-03-31", "shortTermDebt": "10", "longTermDebt": "190"},
                        {"fiscalDateEnding": "2025-03-31", "shortTermDebt": "10", "longTermDebt": "190"},
                    ]},
                },
                "BUBBLE": {
                    "income_statement": {"quarterlyReports": [
                        {"fiscalDateEnding": "2026-03-31", "totalRevenue": "1000", "operatingIncome": "80", "researchAndDevelopment": "300"},
                        {"fiscalDateEnding": "2025-03-31", "totalRevenue": "980", "operatingIncome": "100", "researchAndDevelopment": "160"},
                    ]},
                    "cash_flow": {"quarterlyReports": [
                        {"fiscalDateEnding": "2026-03-31", "operatingCashflow": "180", "capitalExpenditures": "-500"},
                        {"fiscalDateEnding": "2025-03-31", "operatingCashflow": "200", "capitalExpenditures": "-150"},
                    ]},
                    "balance_sheet": {"quarterlyReports": [
                        {"fiscalDateEnding": "2026-03-31", "shortTermDebt": "200", "longTermDebt": "800"},
                        {"fiscalDateEnding": "2025-03-31", "shortTermDebt": "100", "longTermDebt": "400"},
                    ]},
                },
            },
        }
        sec_cache = {
            "schema_version": "sec_edgar_cache.v0.1",
            "companies": {
                "GOOD": {"language_markers": {"ai_mentions": 8, "monetization_mentions": 7, "capex_infrastructure_mentions": 2, "energy_constraint_mentions": 0, "obligation_risk_mentions": 0}},
                "BUBBLE": {"language_markers": {"ai_mentions": 9, "monetization_mentions": 0, "capex_infrastructure_mentions": 14, "energy_constraint_mentions": 6, "obligation_risk_mentions": 5}},
            },
        }

        baseline = score_ai_signals.build_cache(alpha_cache, generated_at="2026-05-30T00:00:00Z")
        blended = score_ai_signals.build_cache(alpha_cache, sec_cache=sec_cache, generated_at="2026-05-30T00:00:00Z")

        self.assertEqual(blended["metadata"]["sec_edgar_schema_version"], "sec_edgar_cache.v0.1")
        self.assertEqual(blended["source"], "alpha_vantage_cache+sec_edgar_cache")
        self.assertGreater(blended["signals"]["ai_productivity"]["score"], baseline["signals"]["ai_productivity"]["score"])
        self.assertGreater(blended["signals"]["ai_capex_bubble_risk"]["score"], baseline["signals"]["ai_capex_bubble_risk"]["score"])
        bubble_metrics = next(m for m in blended["company_metrics"] if m["ticker"] == "BUBBLE")
        self.assertGreater(bubble_metrics["sec_language_bubble_risk_score"], 80)
        self.assertIn("SEC filing-language", blended["signals"]["ai_capex_bubble_risk"]["note"])

    def test_sec_language_cache_with_no_alpha_overlap_does_not_claim_sec_source(self):
        alpha_cache = {
            "schema_version": "alpha_vantage_cache.v0.1",
            "generated_at": "2026-05-30T00:00:00Z",
            "tickers": {
                "MSFT": {
                    "income_statement": {"quarterlyReports": [{"fiscalDateEnding": "2026-03-31", "totalRevenue": "1000", "operatingIncome": "250"}]},
                    "cash_flow": {"quarterlyReports": [{"fiscalDateEnding": "2026-03-31", "operatingCashflow": "300", "capitalExpenditures": "-100"}]},
                    "balance_sheet": {"quarterlyReports": [{"fiscalDateEnding": "2026-03-31", "shortTermDebt": "50", "longTermDebt": "450"}]},
                }
            },
        }
        sec_cache = {
            "schema_version": "sec_edgar_cache.v0.1",
            "companies": {
                "NVDA": {"language_markers": {"ai_mentions": 10, "monetization_mentions": 10, "capex_infrastructure_mentions": 10}},
            },
        }

        cache = score_ai_signals.build_cache(alpha_cache, sec_cache=sec_cache, generated_at="2026-05-30T00:00:00Z")

        self.assertEqual(cache["source"], "alpha_vantage_cache")
        self.assertEqual(cache["metadata"]["sec_language_company_count"], 0)
        self.assertIsNone(cache["metadata"]["sec_edgar_schema_version"])

    def test_company_metrics_do_not_label_adjacent_quarter_as_yoy(self):
        ticker_payload = {
            "income_statement": {"quarterlyReports": [
                {"fiscalDateEnding": "2026-03-31", "totalRevenue": "1000", "operatingIncome": "250"},
                {"fiscalDateEnding": "2025-12-31", "totalRevenue": "900", "operatingIncome": "220"},
            ]},
            "cash_flow": {"quarterlyReports": [
                {"fiscalDateEnding": "2026-03-31", "operatingCashflow": "300", "capitalExpenditures": "-200"},
                {"fiscalDateEnding": "2025-12-31", "operatingCashflow": "260", "capitalExpenditures": "-100"},
            ]},
            "balance_sheet": {"quarterlyReports": [
                {"fiscalDateEnding": "2026-03-31", "shortTermDebt": "50", "longTermDebt": "450"},
                {"fiscalDateEnding": "2025-12-31", "shortTermDebt": "50", "longTermDebt": "350"},
            ]},
        }

        metrics = score_ai_signals.company_metrics("MSFT", ticker_payload)

        self.assertIsNone(metrics["revenue_yoy_pct"])
        self.assertIsNone(metrics["capex_yoy_pct"])
        self.assertIsNone(metrics["debt_yoy_pct"])

    def test_company_metrics_uses_total_debt_without_double_counting_long_term_debt(self):
        ticker_payload = {
            "income_statement": {"quarterlyReports": [
                {"fiscalDateEnding": "2026-03-31", "totalRevenue": "1000", "operatingIncome": "250"},
                {"fiscalDateEnding": "2025-03-31", "totalRevenue": "900", "operatingIncome": "200"},
            ]},
            "cash_flow": {"quarterlyReports": [
                {"fiscalDateEnding": "2026-03-31", "operatingCashflow": "300", "capitalExpenditures": "-100"},
                {"fiscalDateEnding": "2025-03-31", "operatingCashflow": "250", "capitalExpenditures": "-90"},
            ]},
            "balance_sheet": {"quarterlyReports": [
                {"fiscalDateEnding": "2026-03-31", "shortLongTermDebtTotal": "1000", "longTermDebt": "800"},
                {"fiscalDateEnding": "2025-03-31", "shortLongTermDebtTotal": "500", "longTermDebt": "400"},
            ]},
        }

        metrics = score_ai_signals.company_metrics("MSFT", ticker_payload)

        self.assertEqual(metrics["debt"], 1000.0)
        self.assertAlmostEqual(metrics["debt_yoy_pct"], 100.0)


class SecEdgarCacheUpdaterTests(unittest.TestCase):
    def test_latest_filings_selects_recent_10k_and_10q(self):
        submissions = {
            "filings": {
                "recent": {
                    "form": ["8-K", "10-Q", "10-K"],
                    "accessionNumber": ["0000000000-26-000001", "0000789019-26-000010", "0000789019-25-000100"],
                    "filingDate": ["2026-05-01", "2026-04-25", "2025-07-30"],
                    "primaryDocument": ["x.htm", "msft-20260331.htm", "msft-20250630.htm"],
                }
            }
        }

        latest = update_sec_edgar_cache.latest_filings("0000789019", submissions)

        self.assertEqual(latest["10-Q"]["filing_date"], "2026-04-25")
        self.assertEqual(latest["10-K"]["filing_date"], "2025-07-30")
        self.assertIn("/Archives/edgar/data/789019/000078901926000010/msft-20260331.htm", latest["10-Q"]["url"])

    def test_company_fact_subset_keeps_only_relevant_us_gaap_tags(self):
        facts = {
            "facts": {
                "us-gaap": {
                    "Revenues": {"units": {"USD": [{"end": "2026-03-31", "val": 700}]}},
                    "CapitalExpendituresIncurredButNotYetPaid": {"units": {"USD": [{"end": "2026-03-31", "val": 30}]}},
                    "Assets": {"units": {"USD": [{"end": "2026-03-31", "val": 1000}]}},
                }
            }
        }

        subset = update_sec_edgar_cache.company_fact_subset(facts)

        self.assertEqual(set(subset), {"Revenues", "CapitalExpendituresIncurredButNotYetPaid"})
        self.assertEqual(subset["Revenues"]["units"]["USD"][0]["val"], 700)

    def test_extract_language_markers_counts_ai_capex_and_power_terms(self):
        text = """
        We are investing in AI infrastructure, GPUs, and data centers.
        Power availability and electricity constraints may affect data center capacity.
        We expect AI monetization through cloud revenue over time.
        """

        markers = update_sec_edgar_cache.extract_language_markers(text)

        self.assertGreaterEqual(markers["ai_mentions"], 2)
        self.assertGreaterEqual(markers["capex_infrastructure_mentions"], 3)
        self.assertGreaterEqual(markers["energy_constraint_mentions"], 2)
        self.assertLessEqual(len(markers["snippets"]), 5)

    def test_build_cache_uses_user_agent_and_never_stores_it(self):
        calls = []

        def fake_fetch_json(url, user_agent):
            calls.append((url, user_agent))
            if "submissions" in url:
                return {
                    "name": "MICROSOFT CORP",
                    "filings": {"recent": {"form": [], "accessionNumber": [], "filingDate": [], "primaryDocument": []}},
                }
            return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": []}}}}}

        cache = update_sec_edgar_cache.build_cache(
            tickers=["MSFT"],
            user_agent="Doug Test user@example.com",
            fetch_json=fake_fetch_json,
        )

        self.assertTrue(all(call[1] == "Doug Test user@example.com" for call in calls))
        self.assertEqual(cache["schema_version"], "sec_edgar_cache.v0.1")
        self.assertEqual(cache["companies"]["MSFT"]["cik"], "0000789019")
        self.assertNotIn("user@example.com", json.dumps(cache))

    def test_build_cache_redacts_user_agent_from_error_messages(self):
        def leaking_fetch_json(url, user_agent):
            raise RuntimeError(f"provider rejected UA {user_agent}")

        cache = update_sec_edgar_cache.build_cache(
            tickers=["MSFT"],
            user_agent="Doug Test user@example.com",
            fetch_json=leaking_fetch_json,
        )

        errors = json.dumps(cache["errors"])
        self.assertIn("[USER_AGENT_REDACTED]", errors)
        self.assertNotIn("Doug Test", errors)
        self.assertNotIn("user@example.com", errors)

    def test_validate_user_agent_requires_contactable_identity(self):
        with self.assertRaises(ValueError):
            update_sec_edgar_cache.validate_user_agent("")
        with self.assertRaises(ValueError):
            update_sec_edgar_cache.validate_user_agent("Bitcoin-Power-Law AIM research https://github.com/LoknLod/Bitcoin-Power-Law")
        self.assertEqual(
            update_sec_edgar_cache.validate_user_agent("Doug Test user@example.com"),
            "Doug Test user@example.com",
        )

    def test_build_cache_records_unknown_ticker_error_without_network(self):
        def never_fetch_json(url, user_agent):
            raise AssertionError("network should not be called for unknown ticker")

        cache = update_sec_edgar_cache.build_cache(
            tickers=["NOPE"],
            user_agent="Doug Test user@example.com",
            fetch_json=never_fetch_json,
        )

        self.assertIn("NOPE", cache["errors"])
        self.assertEqual(cache["companies"], {})


if __name__ == "__main__":
    unittest.main()
