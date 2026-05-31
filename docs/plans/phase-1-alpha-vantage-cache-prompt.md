# Phase 1 Alpha Vantage Cache Buildout Prompt

> **For Hermes/Codex:** Implement this with strict TDD. Write failing tests first, verify RED, implement the smallest code to pass, then run the full suite. Do not expose API keys in committed files, logs, browser JavaScript, or generated JSON.

## Goal

Build the AIM widget's Phase 1 financial-data spine: a server-side Alpha Vantage cache updater that collects normalized quarterly/annual fundamentals for the first AI infrastructure watchlist and writes `alpha-vantage-cache.json` for later AI productivity and AI capex-bubble scoring.

## Why

The Dashboard currently has AI starter placeholders. We need real financial inputs before replacing them with scored signals. Alpha Vantage provides the normalized financial table; SEC EDGAR will later provide filing-language context.

## Scope

Create `scripts/update_alpha_vantage_cache.py` and tests. Do not wire dashboard scoring yet.

## Data sources

Use Alpha Vantage functions:

- `OVERVIEW`
- `INCOME_STATEMENT`
- `BALANCE_SHEET`
- `CASH_FLOW`
- `EARNINGS`

Use env keys in this priority order:

1. `ALPHA_VANTAGE_STOCK_API`
2. `ALPHAVANTAGE_API_KEY`

Ignore typo aliases except as fallback compatibility; do not introduce new typo names.

## Target tickers v0

```text
MSFT, GOOGL, AMZN, META, NVDA, AVGO, AMD, ORCL, TSM
```

## Cache contract

Write `alpha-vantage-cache.json`:

```json
{
  "schema_version": "alpha_vantage_cache.v0.1",
  "generated_at": "...Z",
  "source": "alpha_vantage",
  "tickers": {
    "MSFT": {
      "overview": {},
      "income_statement": {"annualReports": [], "quarterlyReports": []},
      "balance_sheet": {"annualReports": [], "quarterlyReports": []},
      "cash_flow": {"annualReports": [], "quarterlyReports": []},
      "earnings": {"annualEarnings": [], "quarterlyEarnings": []},
      "errors": []
    }
  },
  "errors": [],
  "metadata": {
    "ticker_count": 9,
    "updated_tickers": 9,
    "error_count": 0,
    "functions": ["OVERVIEW", "INCOME_STATEMENT", "BALANCE_SHEET", "CASH_FLOW", "EARNINGS"]
  }
}
```

## Requirements

1. Pure stdlib Python; match existing repo style.
2. Provide CLI:

```bash
python3 scripts/update_alpha_vantage_cache.py --output alpha-vantage-cache.json
python3 scripts/update_alpha_vantage_cache.py --tickers MSFT,NVDA --output /tmp/alpha.json
```

3. Rotate available API keys per request.
4. Respect Alpha Vantage rate limits with a configurable sleep:

```bash
--sleep-seconds 12
```

Default may be modest, but tests must use `0`.

5. Detect Alpha Vantage limit/error payloads from keys:

```text
Note
Information
Error Message
```

Record them as errors; do not crash the whole run unless no keys exist.

6. Generated cache must never include API keys or env var values.
7. Tests must use fake fetchers; no live network in unit tests.
8. Full test suite must pass.

## Tests to add

Add to `tests/test_cache_updaters.py`:

- Alpha Vantage key collection prioritizes `ALPHA_VANTAGE_STOCK_API` then `ALPHAVANTAGE_API_KEY`, dedupes identical values, and tolerates missing env.
- Alpha Vantage endpoint normalization stores all five functions under stable snake_case keys.
- Builder rotates keys while fetching multiple functions/tickers.
- Limit/error payloads become ticker/function errors, not leaked secrets or crashes.

## Verification

Run:

```bash
python3 -m unittest tests.test_cache_updaters -v
python3 -m unittest discover -s tests -v
python3 scripts/update_alpha_vantage_cache.py --tickers MSFT,NVDA --sleep-seconds 13 --output /tmp/alpha-vantage-cache.json
python3 - <<'PY'
import json
p='/tmp/alpha-vantage-cache.json'
data=json.load(open(p))
print(data['schema_version'], data['metadata']['ticker_count'], data['metadata']['error_count'])
print(sorted(data['tickers']))
PY
```

## Out of scope

- No SEC EDGAR parsing.
- No EIA.
- No AIM score changes.
- No dashboard UI changes.
- No client/browser API calls.
