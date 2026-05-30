# AIM-5 Macro Tracker

## Purpose

AIM means AI Interregnum Model. The tracker is a 5-10 year decision-support cockpit for comparing five pressures:

- AI productivity boom
- AI capex bubble / malinvestment risk
- monetary reset / dollar credit stress
- energy, power, and grid bottlenecks
- BTC/gold hard-money repricing

This is not a trade executor. It does not connect to brokers, accounts, wallets, or secrets.

## AIM-5 Frame

The base allocation frame shown on the active dashboard, `index.html`, is:

- AI Productive Equity: 40%
- Hard Money / Monetary Reset Hedge: 25%
- Energy / Power / Real Assets: 15%
- Runway / Optionality: 15%
- Spec AIM Venture Basket: 5%

Language rule:

- BTC is the spear.
- Gold is the shield.
- The barbell is the base posture unless signals clearly tilt.

## Cache Contract

`scripts/score_aim_macro.py` is the canonical generator for `aim-cache.json`.
`index.html` reads that cache for the active BTC/AIM Dashboard. If the cache cannot
be loaded, the page falls back to embedded demo-only data and displays a visible
demo warning. The former standalone AIM page lives at `archive/aim.html` only for
historical/reference use.

Source cache updaters:

- `scripts/update_fred_cache.py`: refreshes `fred-cache.json` from public FRED graph CSV endpoints, with no API key.
- `scripts/update_market_cache.py`: refreshes `market-cache.json` from public BTC/PAXG price endpoints, with no API key.

For committed/cache generation, use an explicit as-of date:

```bash
python3 scripts/update_fred_cache.py --as-of YYYY-MM-DD
python3 scripts/update_market_cache.py --as-of YYYY-MM-DD
python3 scripts/score_aim_macro.py --as-of YYYY-MM-DD
```

`--as-of` sets `generated_at` to exactly `YYYY-MM-DDT00:00:00Z`, filters local
observations to that date, and calculates freshness as of that date. Running the
script without `--as-of` uses the current UTC date for interactive manual runs.

Required compatible fields:

- `schema_version`: currently `aim_macro_cache.v0.1`
- `scoring_version`: currently `aim_macro_scoring.v0.2`
- `generated_at`: ISO timestamp for the generated local cache
- `source`: source label such as `local_fred_cache` or `starter_static_cache`
- `freshness`: `local_cache`, `stale`, or `demo`
- `posture`: object with `key`, `label`, and `explanation`
- `scores`: five regime objects with `score`, `confidence`, and `interpretation`
- `aim5_allocation`: five target sleeve objects
- `signals`: full audit ledger entries with `name`, `regime`, `score`, `weight`, `direction`, `source`, `as_of`, `note`, and freshness metadata where applicable
- `dashboard_signals`: smaller Dashboard watchlist: AI productivity, AI capex risk, BTC power-law gap, BTC/gold ratio, and world credit growth
- `debate_question`: current question to keep the thesis falsifiable

## First-Pass Scoring

Run:

```bash
python3 scripts/update_fred_cache.py --as-of 2026-05-29
python3 scripts/update_market_cache.py --as-of 2026-05-29
python3 scripts/score_aim_macro.py --as-of 2026-05-29
```

To replace the starter AI signals with Alpha Vantage-derived financial scores,
build the AI sidecar cache and opt in explicitly:

```bash
python3 scripts/update_alpha_vantage_cache.py
python3 scripts/score_ai_signals.py
python3 scripts/score_aim_macro.py --as-of 2026-05-29 --ai-signals-cache ai-signals-cache.json
```

The explicit `--ai-signals-cache` flag is deliberate: `ai-signals-cache.json` is
a local ignored API-derived cache, so it must not silently alter the tracked
`aim-cache.json` artifact just because it exists on one machine.

The scripts use only Python standard library modules. The source cache updaters
make public, no-key network requests; the scorer itself reads only local JSON
files and does not make network requests.

Current model:

- AI Productivity: low-confidence starter score by default; medium-confidence Alpha Vantage-derived score when a valid `ai-signals-cache.json` is explicitly supplied.
- AI Bubble Risk: low-confidence starter score by default; medium-confidence Alpha Vantage-derived capex/malinvestment score when a valid `ai-signals-cache.json` is explicitly supplied.
- Monetary Reset Risk: deterministic score from local `fred-cache.json` when usable, anchored on world credit growth rather than U.S. M2.
- Energy Bottleneck: low-confidence qualitative starter score until power/grid data are added.
- Hard Money Repricing: deterministic score from local `market-cache.json` when BTC/PAXG prices are usable, plus the repo BTC power-law fair-value formula.

FRED series used when present:

- `WM2NS`: M2 money stock — secondary U.S. liquidity context
- `WALCL`: Fed balance sheet
- `RRPONTSYD`: overnight reverse repo
- `WTREGEN`: Treasury General Account
- `QUSCAMUSDA`: U.S. total credit to non-financial sector
- `Q5ACAMUSDA`: total reporting countries credit to non-financial sector — headline world-credit signal
- `GFDEBTN`: federal debt
- `A091RC1Q027SBEA`: federal government interest payments
- `DGS10`: 10Y Treasury yield
- `DFII10`: 10Y real yield
- `T10YIE`: 10Y breakeven inflation
- `BAMLH0A0HYM2`: high-yield option-adjusted spread

Market cache assets used when present:

- `btc_usd`: BTC/USD spot
- `gold_usd`: PAXG/USD gold proxy

The monetary score is a simple weighted average of transparent signal scores.
The headline paper-claims input is `World Credit Growth`, using `Q5ACAMUSDA`
as a 3-year annualized growth signal. This is the closer match to the 2029 /
Stansberry-style thesis than a domestic M2 chart. U.S. M2 remains in the model,
but with low weight as context rather than the cockpit anchor.
Each FRED-derived signal carries `freshness` and `age_days`. Overall cache
freshness is coverage-aware across both FRED and market cache inputs:

- `demo`: no useful weighted FRED or market data is available.
- `stale`: FRED or market data is missing or stale for required weighted signals.
- `local_cache`: required FRED and market signals are available and fresh enough as of `--as-of`.

Daily/weekly FRED signals use a 45-day freshness window. Quarterly credit and
fiscal series use a 370-day window so slower BIS and fiscal reporting lag is not
mislabeled as stale. Market prices use a 3-day freshness window.

Net Liquidity uses the same normalized proxy as `macro.html`:

```text
WALCL / 1000 - RRPONTSYD - WTREGEN / 1000
```

It compares the latest available proxy with a roughly 90-day prior comparable
observation when possible. The score is:

- `<= -5%`: 35
- `-5%` to `0%`: 45
- `0%` to `+5%`: 55
- `> +5%`: 65

If comparable component data is insufficient, the Net Liquidity signal is
informational with weight `0` and score `50`.

Starter AI and energy signals are marked `starter` rather than being counted as
fresh source data. A supplied AI cache is accepted only when it uses the expected
schema, has at least one company, has numeric AI scores, and is fresh by the
underlying company metric dates; otherwise the model falls back to the starter
AI signals. If `market-cache.json` is missing, hard-money repricing signals are
marked `missing` and weighted at `0`.

Dashboard rule: the active Dashboard shows only `dashboard_signals`, a small
watchlist of the two AI scores when available — otherwise the two AI starters —
the best BTC/gold hard-money signals, and the most important macro signal. The
full raw ledger remains in `signals` for auditability; the Macro page owns the
broader monetary reset subset.

Hard-money repricing uses:

- BTC power-law fair-value gap using `10^(-17.01 + 5.82 * log10(daysSinceGenesis))` from genesis date `2009-01-03`
- BTC/gold ratio using BTC/USD over PAXG/USD
- PAXG gold proxy price

## Posture Rules

The script defaults to `Barbell`: AI boom and monetary stress can coexist.

Simple starter tilts:

- `AI Growth Tilt`: productivity is high, bubble risk is contained, and monetary stress is low.
- `Hard-Money Barbell`: monetary reset, hard-money repricing, or energy bottleneck pressure is elevated.
- `Defensive Barbell`: bubble risk and monetary stress are both elevated.

These are labels for discussion and review, not automatic reallocations.

## TODOs

- Wire SEC filing language and segment-level evidence into the AI productivity/capex scores.
- Add power price, grid queue, data center load, and transformer/turbine bottleneck signals.
- Add additional hard-money context such as BTC realized-price bands or gold lease-rate stress without broker, wallet, or trade behavior.
- Add visual regression checks once this static site has a stable browser test harness.

## Security

AIM adds no new secrets. The existing `macro.html` page has a browser-exposed
FRED API key from prior work; browser-exposed keys are not secrets and should be
moved to cache-only fetching in a future hardening pass. This AIM pass does not
change that key or break the existing macro page.

## Rule

Decision support only. No trades, reallocations, or broker/account actions without explicit Doug approval.
