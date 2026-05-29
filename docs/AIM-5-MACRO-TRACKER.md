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

The base allocation frame shown in `aim.html` is:

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
`aim.html` only reads that cache. If the cache cannot be loaded, the page falls
back to embedded demo-only data and displays a visible demo warning.

For committed/cache generation, use an explicit as-of date:

```bash
python3 scripts/score_aim_macro.py --as-of YYYY-MM-DD
```

`--as-of` sets `generated_at` to exactly `YYYY-MM-DDT00:00:00Z`, filters local
observations to that date, and calculates freshness as of that date. Running the
script without `--as-of` uses the current UTC date for interactive manual runs.

Required compatible fields:

- `schema_version`: currently `aim_macro_cache.v0.1`
- `scoring_version`: currently `aim_macro_scoring.v0.1`
- `generated_at`: ISO timestamp for the generated local cache
- `source`: source label such as `local_fred_cache` or `starter_static_cache`
- `freshness`: `local_cache`, `stale`, or `demo`
- `posture`: object with `key`, `label`, and `explanation`
- `scores`: five regime objects with `score`, `confidence`, and `interpretation`
- `aim5_allocation`: five target sleeve objects
- `signals`: ledger entries with `name`, `regime`, `score`, `weight`, `direction`, `source`, `as_of`, `note`, and freshness metadata where applicable
- `debate_question`: current question to keep the thesis falsifiable

## First-Pass Scoring

Run:

```bash
python3 scripts/score_aim_macro.py --as-of 2026-05-29
```

The script uses only Python standard library modules. It does not make network requests and does not require API keys.

Current model:

- AI Productivity: low-confidence starter score until verified AI revenue, margin, and productivity data are added.
- AI Bubble Risk: low-confidence starter score until hyperscaler capex, utilization, and financing quality data are added.
- Monetary Reset Risk: deterministic score from local `fred-cache.json` when usable.
- Energy Bottleneck: low-confidence qualitative starter score until power/grid data are added.
- Hard Money Repricing: low-confidence BTC power-law fair value anchor from the existing repo formula; live BTC price source is pending and no valuation spread is faked.

FRED series used when present:

- `WM2NS`: M2 money stock
- `WALCL`: Fed balance sheet
- `RRPONTSYD`: overnight reverse repo
- `WTREGEN`: Treasury General Account

The monetary score is a simple weighted average of transparent signal scores.
Each FRED-derived signal carries `freshness` and `age_days`. Overall cache
freshness is coverage-aware:

- `demo`: no useful weighted FRED monetary data is available.
- `stale`: any required FRED series used by a weighted monetary signal is older than 45 days as of `--as-of`.
- `local_cache`: all required FRED series used by weighted monetary signals are within 45 days as of `--as-of`.

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

Starter AI, energy, and hard-money anchor signals are marked `starter` rather
than being counted as fresh market data.

## Posture Rules

The script defaults to `Barbell`: AI boom and monetary stress can coexist.

Simple starter tilts:

- `AI Growth Tilt`: productivity is high, bubble risk is contained, and monetary stress is low.
- `Hard-Money Barbell`: monetary reset, hard-money repricing, or energy bottleneck pressure is elevated.
- `Defensive Barbell`: bubble risk and monetary stress are both elevated.

These are labels for discussion and review, not automatic reallocations.

## TODOs

- Add verified AI capex, AI revenue, utilization, and productivity sources.
- Add power price, grid queue, data center load, and transformer/turbine bottleneck signals.
- Add live BTC price and gold hard-money repricing source data without broker, wallet, or trade behavior.
- Add visual regression checks once this static site has a stable browser test harness.

## Security

AIM adds no new secrets. The existing `macro.html` page has a browser-exposed
FRED API key from prior work; browser-exposed keys are not secrets and should be
moved to cache-only fetching in a future hardening pass. This AIM pass does not
change that key or break the existing macro page.

## Rule

Decision support only. No trades, reallocations, or broker/account actions without explicit Doug approval.
