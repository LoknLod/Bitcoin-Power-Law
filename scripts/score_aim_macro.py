#!/usr/bin/env python3
"""Build the local AIM Macro Cockpit cache.

This first-pass scorer is intentionally simple: it uses only local files and
Python's standard library, avoids network access, and labels weak inputs as
low-confidence rather than pretending precision.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
FRED_CACHE = ROOT / "fred-cache.json"
MARKET_CACHE = ROOT / "market-cache.json"
AIM_CACHE = ROOT / "aim-cache.json"
SCHEMA_VERSION = "aim_macro_cache.v0.1"
SCORING_VERSION = "aim_macro_scoring.v0.2"
FRESHNESS_MAX_AGE_DAYS = 45
QUARTERLY_FRESHNESS_MAX_AGE_DAYS = 370
MARKET_FRESHNESS_MAX_AGE_DAYS = 3
NET_LIQUIDITY_LOOKBACK_DAYS = 90
NET_LIQUIDITY_LOOKBACK_TOLERANCE_DAYS = 14
BTC_GENESIS = date(2009, 1, 3)
BTC_POWER_LAW_A = -17.01
BTC_POWER_LAW_B = 5.82
DEBATE_QUESTION = "Is AI producing real productivity faster than credit/fiscal stress is degrading money?"

QUARTERLY_SERIES = {
    "QUSCAMUSDA",
    "Q5ACAMUSDA",
    "GFDEBTN",
    "A091RC1Q027SBEA",
}


AIM5_ALLOCATION = [
    {
        "sleeve": "AI Productive Equity",
        "target_pct": 40,
        "role": "Own profitable AI platforms and infrastructure.",
    },
    {
        "sleeve": "Hard Money / Monetary Reset Hedge",
        "target_pct": 25,
        "role": "BTC spear, gold shield.",
    },
    {
        "sleeve": "Energy / Power / Real Assets",
        "target_pct": 15,
        "role": "Own physical bottlenecks behind AI and hard money.",
    },
    {
        "sleeve": "Runway / Optionality",
        "target_pct": 15,
        "role": "Dry powder and forced-selling protection.",
    },
    {
        "sleeve": "Spec AIM Venture Basket",
        "target_pct": 5,
        "role": "Small sandbox for asymmetric moonshots.",
    },
]


Observation = Tuple[date, float]


def default_as_of() -> date:
    return datetime.now(timezone.utc).date()


def utc_day_stamp(as_of: date) -> str:
    """Stable within a UTC day, avoiding noisy diffs from second-level clocks."""
    return f"{as_of.isoformat()}T00:00:00Z"


def read_json(path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return None, f"{path.name} not found"
    except json.JSONDecodeError as exc:
        return None, f"{path.name} malformed: {exc}"
    except OSError as exc:
        return None, f"{path.name} unreadable: {exc}"

    if not isinstance(data, dict):
        return None, f"{path.name} root is not an object"
    return data, None


def parse_cache_date(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) >= 10:
        text = text[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def parse_observations(cache: Dict[str, Any], series_id: str, through: Optional[date] = None) -> List[Observation]:
    series = cache.get("series", {})
    if not isinstance(series, dict):
        return []

    payload = series.get(series_id)
    if isinstance(payload, dict):
        raw_observations = payload.get("observations", [])
    elif isinstance(payload, list):
        raw_observations = payload
    else:
        raw_observations = []

    observations: List[Observation] = []
    if not isinstance(raw_observations, list):
        return observations

    for item in raw_observations:
        if not isinstance(item, dict):
            continue
        raw_date = item.get("date")
        raw_value = item.get("value")
        if raw_date is None or raw_value in (None, "", "."):
            continue
        try:
            parsed_date = date.fromisoformat(str(raw_date))
            parsed_value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if through is not None and parsed_date > through:
            continue
        observations.append((parsed_date, parsed_value))

    return sorted(observations, key=lambda row: row[0])


def latest(observations: List[Observation]) -> Optional[Observation]:
    return observations[-1] if observations else None


def value_on_or_before(observations: List[Observation], target: date) -> Optional[Observation]:
    candidate: Optional[Observation] = None
    for observed_at, value in observations:
        if observed_at <= target:
            candidate = (observed_at, value)
        else:
            break
    return candidate


def closest_to_date(
    observations: List[Observation],
    target: date,
    tolerance_days: int,
) -> Optional[Observation]:
    if not observations:
        return None

    candidate = min(observations, key=lambda row: abs((row[0] - target).days))
    if abs((candidate[0] - target).days) > tolerance_days:
        return None
    return candidate


def yoy_change_pct(observations: List[Observation]) -> Optional[float]:
    current = latest(observations)
    if current is None:
        return None

    target = current[0] - timedelta(days=365)
    previous = value_on_or_before(observations, target)
    if previous is None or previous[1] == 0:
        return None

    return (current[1] - previous[1]) / previous[1] * 100.0


def growth_pct_near(
    observations: List[Observation],
    current: Observation,
    lookback_days: int,
    tolerance_days: int,
    annualize: bool = False,
) -> Optional[float]:
    previous = closest_to_date(observations, current[0] - timedelta(days=lookback_days), tolerance_days)
    if previous is None or previous[1] == 0:
        return None
    total_growth = (current[1] - previous[1]) / previous[1]
    if not annualize:
        return total_growth * 100.0
    years = max(lookback_days / 365.25, 0.01)
    if previous[1] <= 0 or current[1] <= 0:
        return total_growth * 100.0
    return (math.pow(current[1] / previous[1], 1 / years) - 1) * 100.0


def best_growth_pct(observations: List[Observation]) -> Tuple[Optional[float], str]:
    current = latest(observations)
    if current is None:
        return None, "n/a"

    yoy = growth_pct_near(observations, current, 365, 120)
    if yoy is not None:
        return yoy, "YoY"

    three_year = growth_pct_near(observations, current, 365 * 3, 210, annualize=True)
    if three_year is not None:
        return three_year, "3Y annualized"

    return None, "growth unavailable"


def three_year_annualized_growth_pct(observations: List[Observation]) -> Optional[float]:
    current = latest(observations)
    if current is None:
        return None
    return growth_pct_near(observations, current, 365 * 3, 210, annualize=True)


def clamp_score(value: float) -> int:
    return int(round(max(0.0, min(100.0, value))))


def format_pct(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.1f}%"


def format_billions(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"${value:,.0f}B"


def format_usd_price(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    if value >= 1000:
        return f"${value:,.0f}"
    return f"${value:,.2f}"


def format_ratio(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    if value >= 100:
        return f"{value:,.0f}"
    return f"{value:,.1f}"


def signal_age_days(observed_at: date, as_of: date) -> int:
    return (as_of - observed_at).days


def signal_freshness(observed_at: date, as_of: date, max_age_days: int = FRESHNESS_MAX_AGE_DAYS) -> str:
    age_days = signal_age_days(observed_at, as_of)
    if age_days < 0:
        return "future"
    return "local_cache" if age_days <= max_age_days else "stale"


def net_liquidity_billions(walcl_millions: float, rrp_billions: float, tga_millions: float) -> float:
    return walcl_millions / 1000.0 - rrp_billions - tga_millions / 1000.0


def score_net_liquidity(change_pct: Optional[float]) -> int:
    if change_pct is None:
        return 50
    if change_pct <= -5.0:
        return 35
    if change_pct <= 0.0:
        return 45
    if change_pct <= 5.0:
        return 55
    return 65


def btc_power_law_fair_value(as_of: date) -> Optional[float]:
    days_since_genesis = (as_of - BTC_GENESIS).days
    if days_since_genesis <= 0:
        return None
    return math.pow(10, BTC_POWER_LAW_A + BTC_POWER_LAW_B * math.log10(days_since_genesis))


def score_m2(yoy: Optional[float]) -> int:
    if yoy is None:
        return 55
    if yoy >= 5.0:
        return 72
    if yoy >= 2.0:
        return 62
    if yoy >= 0.0:
        return 55
    return 64


def score_walcl(yoy: Optional[float]) -> int:
    if yoy is None:
        return 52
    if yoy >= 2.0:
        return 64
    if yoy >= 0.0:
        return 56
    if yoy <= -5.0:
        return 58
    return 52


def score_rrp(value_billions: Optional[float]) -> int:
    if value_billions is None:
        return 52
    if value_billions < 100:
        return 72
    if value_billions < 500:
        return 62
    if value_billions < 1000:
        return 52
    return 45


def score_tga(value_billions: Optional[float]) -> int:
    if value_billions is None:
        return 52
    if value_billions >= 750:
        return 66
    if value_billions >= 500:
        return 58
    return 50


def score_credit_growth(growth_pct: Optional[float]) -> int:
    if growth_pct is None:
        return 52
    if growth_pct >= 8.0:
        return 72
    if growth_pct >= 5.0:
        return 64
    if growth_pct >= 2.0:
        return 55
    if growth_pct >= 0.0:
        return 48
    return 58


def score_debt_growth(growth_pct: Optional[float]) -> int:
    if growth_pct is None:
        return 52
    if growth_pct >= 10.0:
        return 78
    if growth_pct >= 6.0:
        return 68
    if growth_pct >= 3.0:
        return 58
    if growth_pct >= 0.0:
        return 50
    return 55


def score_interest_growth(growth_pct: Optional[float]) -> int:
    if growth_pct is None:
        return 52
    if growth_pct >= 25.0:
        return 82
    if growth_pct >= 15.0:
        return 74
    if growth_pct >= 8.0:
        return 65
    if growth_pct >= 0.0:
        return 55
    return 45


def score_real_yield(value_pct: Optional[float]) -> int:
    if value_pct is None:
        return 52
    if value_pct >= 2.5:
        return 75
    if value_pct >= 1.5:
        return 66
    if value_pct >= 0.5:
        return 56
    if value_pct >= 0.0:
        return 50
    return 42


def score_high_yield_spread(value_pct: Optional[float]) -> int:
    if value_pct is None:
        return 52
    if value_pct >= 8.0:
        return 85
    if value_pct >= 6.0:
        return 75
    if value_pct >= 4.5:
        return 65
    if value_pct >= 3.5:
        return 55
    return 45


def score_treasury_yield(value_pct: Optional[float]) -> int:
    if value_pct is None:
        return 52
    if value_pct >= 5.0:
        return 68
    if value_pct >= 4.0:
        return 60
    if value_pct >= 3.0:
        return 52
    return 44


def score_breakeven(value_pct: Optional[float]) -> int:
    if value_pct is None:
        return 52
    if value_pct >= 3.0:
        return 68
    if value_pct >= 2.5:
        return 60
    if value_pct >= 2.0:
        return 52
    return 45


def score_btc_power_gap(gap_pct: Optional[float]) -> int:
    if gap_pct is None:
        return 50
    if gap_pct >= 75.0:
        return 72
    if gap_pct >= 15.0:
        return 62
    if gap_pct >= -25.0:
        return 55
    return 48


def score_btc_gold_ratio(ratio: Optional[float]) -> int:
    if ratio is None:
        return 50
    if ratio >= 50.0:
        return 72
    if ratio >= 25.0:
        return 62
    if ratio >= 10.0:
        return 55
    return 45


def score_gold_price(price: Optional[float]) -> int:
    if price is None:
        return 50
    if price >= 3500.0:
        return 68
    if price >= 2500.0:
        return 60
    if price >= 1800.0:
        return 52
    return 45


def weighted_score(signals: Iterable[Dict[str, Any]], fallback: int = 65) -> int:
    numerator = 0.0
    denominator = 0.0
    for signal in signals:
        try:
            score = float(signal["score"])
            weight = float(signal["weight"])
        except (KeyError, TypeError, ValueError):
            continue
        numerator += score * weight
        denominator += weight
    if denominator == 0:
        return fallback
    return clamp_score(numerator / denominator)


def regime_signal(
    name: str,
    regime: str,
    score: int,
    weight: float,
    direction: str,
    source: str,
    as_of: str,
    note: str,
    freshness: Optional[str] = None,
    age_days: Optional[int] = None,
    value_label: Optional[str] = None,
) -> Dict[str, Any]:
    signal = {
        "name": name,
        "regime": regime,
        "score": clamp_score(score),
        "weight": weight,
        "direction": direction,
        "source": source,
        "as_of": as_of,
        "note": note,
    }
    if freshness is not None:
        signal["freshness"] = freshness
    if age_days is not None:
        signal["age_days"] = age_days
    if value_label is not None:
        signal["value_label"] = value_label
    return signal


def starter_signals(generated_at: str) -> List[Dict[str, Any]]:
    as_of = generated_at[:10]
    return [
        regime_signal(
            "AI Productivity Starter",
            "ai_productivity",
            55,
            0.35,
            "higher supports productive AI boom",
            "starter_static_assumption",
            as_of,
            "Placeholder until verified AI revenue, margin, and productivity series are added.",
            freshness="starter",
        ),
        regime_signal(
            "AI Capex Bubble Starter",
            "ai_bubble_risk",
            50,
            0.35,
            "higher means capex return risk is rising",
            "starter_static_assumption",
            as_of,
            "Placeholder until hyperscaler capex, financing, and utilization data are added.",
            freshness="starter",
        ),
        regime_signal(
            "Energy Bottleneck Starter",
            "energy_bottleneck",
            60,
            0.30,
            "higher means power constraints matter more",
            "starter_static_assumption",
            as_of,
            "Qualitative starter: AI data centers and Bitcoin mining both expose power constraints.",
            freshness="starter",
        ),
    ]


DASHBOARD_SIGNAL_ORDER = [
    "AI Productivity Starter",
    "AI Capex Bubble Starter",
    "BTC Power Law Fair Value Gap",
    "BTC/Gold Ratio",
    "World Credit Growth",
]


def dashboard_signal_watchlist(cache: Dict[str, Any]) -> List[Dict[str, Any]]:
    signals = cache.get("signals", []) if isinstance(cache, dict) else []
    if not isinstance(signals, list):
        return []
    by_name = {signal.get("name"): signal for signal in signals if isinstance(signal, dict)}
    return [by_name[name] for name in DASHBOARD_SIGNAL_ORDER if name in by_name]


def market_price(cache: Optional[Dict[str, Any]], asset_key: str) -> Tuple[Optional[float], Optional[date], str]:
    if not isinstance(cache, dict):
        return None, None, "market-cache.json not found"
    assets = cache.get("assets", {})
    if not isinstance(assets, dict):
        return None, None, "market cache assets missing"
    asset = assets.get(asset_key)
    if not isinstance(asset, dict):
        return None, None, f"{asset_key} missing"
    raw_price = asset.get("price")
    try:
        price = float(raw_price)
    except (TypeError, ValueError):
        return None, parse_cache_date(asset.get("as_of")), f"{asset_key} price invalid"
    if not math.isfinite(price) or price <= 0:
        return None, parse_cache_date(asset.get("as_of")), f"{asset_key} price missing"
    return price, parse_cache_date(asset.get("as_of")), str(asset.get("source") or "market_cache")


def market_freshness(observed_at: Optional[date], as_of: date) -> Tuple[str, Optional[int]]:
    if observed_at is None:
        return "missing", None
    return signal_freshness(observed_at, as_of, MARKET_FRESHNESS_MAX_AGE_DAYS), signal_age_days(observed_at, as_of)


def combined_market_freshness(observed_dates: Iterable[Optional[date]], as_of: date) -> Tuple[str, Optional[int], Optional[date]]:
    dates = [observed_at for observed_at in observed_dates if observed_at is not None]
    if not dates:
        return "missing", None, None
    if any(observed_at > as_of for observed_at in dates):
        observed_at = max(dates)
        return "future", signal_age_days(observed_at, as_of), observed_at
    observed_at = min(dates)
    freshness, age_days = market_freshness(observed_at, as_of)
    return freshness, age_days, observed_at


def missing_hard_money_signal(name: str, as_of: date, reason: str) -> Dict[str, Any]:
    return regime_signal(
        name,
        "hard_money_repricing",
        50,
        0.0,
        "missing market input; not scored",
        "market_cache",
        as_of.isoformat(),
        f"{reason}; hard-money repricing signal remains low-confidence until market-cache.json is refreshed.",
        freshness="missing",
    )


def build_hard_money_signals(
    market_cache: Optional[Dict[str, Any]],
    as_of: date,
    market_error: Optional[str] = None,
) -> List[Dict[str, Any]]:
    fair_value = btc_power_law_fair_value(as_of)
    btc_price, btc_date, btc_source = market_price(market_cache, "btc_usd")
    gold_price, gold_date, gold_source = market_price(market_cache, "gold_usd")
    missing_reason = market_error or "market-cache.json missing or incomplete"

    signals: List[Dict[str, Any]] = []

    if btc_price is not None and fair_value is not None:
        gap_pct = (btc_price - fair_value) / fair_value * 100.0
        freshness, age_days = market_freshness(btc_date, as_of)
        signals.append(
            regime_signal(
                "BTC Power Law Fair Value Gap",
                "hard_money_repricing",
                score_btc_power_gap(gap_pct),
                0.45,
                "higher means BTC is repricing above the time-based power-law anchor",
                btc_source,
                btc_date.isoformat() if btc_date else as_of.isoformat(),
                (
                    f"BTC spot is {format_usd_price(btc_price)} versus a power-law anchor of "
                    f"{format_usd_price(fair_value)}, a {format_pct(gap_pct)} gap."
                ),
                freshness=freshness,
                age_days=age_days,
                value_label=format_pct(gap_pct),
            )
        )
    else:
        reason = "BTC spot price unavailable" if btc_price is None else "BTC power-law anchor unavailable before genesis"
        signals.append(missing_hard_money_signal("BTC Power Law Fair Value Gap", as_of, reason or missing_reason))

    if btc_price is not None and gold_price is not None:
        ratio = btc_price / gold_price
        freshness, age_days, observed_at = combined_market_freshness((btc_date, gold_date), as_of)
        signals.append(
            regime_signal(
                "BTC/Gold Ratio",
                "hard_money_repricing",
                score_btc_gold_ratio(ratio),
                0.35,
                "higher means BTC is repricing versus the gold proxy",
                "market_cache BTC/USD over PAXG/USD",
                observed_at.isoformat() if observed_at else as_of.isoformat(),
                f"BTC buys roughly {format_ratio(ratio)} ounces of the PAXG gold proxy.",
                freshness=freshness,
                age_days=age_days,
                value_label=f"{format_ratio(ratio)} oz",
            )
        )
    else:
        missing = "BTC spot price unavailable" if btc_price is None else "gold proxy price unavailable"
        signals.append(missing_hard_money_signal("BTC/Gold Ratio", as_of, missing or missing_reason))

    if gold_price is not None:
        freshness, age_days = market_freshness(gold_date, as_of)
        signals.append(
            regime_signal(
                "Gold Proxy Price",
                "hard_money_repricing",
                score_gold_price(gold_price),
                0.20,
                "higher means the gold shield is repricing in USD terms",
                gold_source,
                gold_date.isoformat() if gold_date else as_of.isoformat(),
                f"PAXG gold proxy is {format_usd_price(gold_price)}.",
                freshness=freshness,
                age_days=age_days,
                value_label=format_usd_price(gold_price),
            )
        )
    else:
        signals.append(missing_hard_money_signal("Gold Proxy Price", as_of, "gold proxy price unavailable" or missing_reason))

    return signals


def series_freshness_days(series_id: str) -> int:
    return QUARTERLY_FRESHNESS_MAX_AGE_DAYS if series_id in QUARTERLY_SERIES else FRESHNESS_MAX_AGE_DAYS


def fred_freshness(series_id: str, observed_at: date, as_of: date) -> str:
    return signal_freshness(observed_at, as_of, series_freshness_days(series_id))


def missing_monetary_signal(name: str, series_id: str, as_of: date, note: str) -> Dict[str, Any]:
    return regime_signal(
        name,
        "monetary_reset",
        50,
        0.0,
        "missing FRED input; not scored",
        f"FRED {series_id}",
        as_of.isoformat(),
        note,
        freshness="missing",
    )


def add_growth_signal(
    signals: List[Dict[str, Any]],
    observations: Dict[str, List[Observation]],
    series_id: str,
    name: str,
    weight: float,
    scorer: Any,
    direction: str,
    note_prefix: str,
    as_of: date,
) -> None:
    series_observations = observations[series_id]
    series_latest = latest(series_observations)
    growth, growth_label = best_growth_pct(series_observations)
    if series_latest is None:
        signals.append(
            missing_monetary_signal(
                name,
                series_id,
                as_of,
                f"{note_prefix} is unavailable because FRED {series_id} has no usable observations.",
            )
        )
        return

    signals.append(
        regime_signal(
            name,
            "monetary_reset",
            scorer(growth),
            weight if growth is not None else 0.0,
            direction,
            f"FRED {series_id}",
            series_latest[0].isoformat(),
            f"{note_prefix} is {format_pct(growth)} {growth_label} at {series_latest[1]:,.1f}.",
            freshness=fred_freshness(series_id, series_latest[0], as_of),
            age_days=signal_age_days(series_latest[0], as_of),
            value_label=f"{format_pct(growth)} {growth_label}",
        )
    )


def add_level_signal(
    signals: List[Dict[str, Any]],
    observations: Dict[str, List[Observation]],
    series_id: str,
    name: str,
    weight: float,
    scorer: Any,
    direction: str,
    note_template: str,
    as_of: date,
) -> None:
    series_latest = latest(observations[series_id])
    if series_latest is None:
        signals.append(
            missing_monetary_signal(
                name,
                series_id,
                as_of,
                f"{name} is unavailable because FRED {series_id} has no usable observations.",
            )
        )
        return

    signals.append(
        regime_signal(
            name,
            "monetary_reset",
            scorer(series_latest[1]),
            weight,
            direction,
            f"FRED {series_id}",
            series_latest[0].isoformat(),
            note_template.format(value=series_latest[1]),
            freshness=fred_freshness(series_id, series_latest[0], as_of),
            age_days=signal_age_days(series_latest[0], as_of),
            value_label=f"{series_latest[1]:.2f}%",
        )
    )


def build_monetary_signals(cache: Dict[str, Any], as_of: date) -> Tuple[List[Dict[str, Any]], Optional[date]]:
    observations = {
        "WM2NS": parse_observations(cache, "WM2NS", through=as_of),
        "WALCL": parse_observations(cache, "WALCL", through=as_of),
        "RRPONTSYD": parse_observations(cache, "RRPONTSYD", through=as_of),
        "WTREGEN": parse_observations(cache, "WTREGEN", through=as_of),
        "QUSCAMUSDA": parse_observations(cache, "QUSCAMUSDA", through=as_of),
        "Q5ACAMUSDA": parse_observations(cache, "Q5ACAMUSDA", through=as_of),
        "GFDEBTN": parse_observations(cache, "GFDEBTN", through=as_of),
        "A091RC1Q027SBEA": parse_observations(cache, "A091RC1Q027SBEA", through=as_of),
        "DGS10": parse_observations(cache, "DGS10", through=as_of),
        "DFII10": parse_observations(cache, "DFII10", through=as_of),
        "T10YIE": parse_observations(cache, "T10YIE", through=as_of),
        "BAMLH0A0HYM2": parse_observations(cache, "BAMLH0A0HYM2", through=as_of),
    }

    latest_dates = [row[0] for rows in observations.values() for row in rows[-1:]]
    newest_date = max(latest_dates) if latest_dates else None
    signals: List[Dict[str, Any]] = []

    wm2_latest = latest(observations["WM2NS"])
    wm2_yoy = yoy_change_pct(observations["WM2NS"])
    if wm2_latest:
        signals.append(
            regime_signal(
                "U.S. M2 Liquidity Context",
                "monetary_reset",
                score_m2(wm2_yoy),
                0.03,
                "domestic liquidity context; not the core world-credit thesis signal",
                "FRED WM2NS",
                wm2_latest[0].isoformat(),
                f"US M2 is {format_pct(wm2_yoy)} YoY at {format_billions(wm2_latest[1])}; useful context, but world credit is the monetary reset anchor.",
                freshness=fred_freshness("WM2NS", wm2_latest[0], as_of),
                age_days=signal_age_days(wm2_latest[0], as_of),
                value_label=format_pct(wm2_yoy),
            )
        )
    else:
        signals.append(
            missing_monetary_signal(
                "U.S. M2 Liquidity Context",
                "WM2NS",
                as_of,
                "US M2 context is unavailable because FRED WM2NS has no usable observations.",
            )
        )

    walcl_latest = latest(observations["WALCL"])
    walcl_yoy = yoy_change_pct(observations["WALCL"])
    if walcl_latest:
        walcl_billions = walcl_latest[1] / 1000.0
        signals.append(
            regime_signal(
                "Fed Balance Sheet YoY",
                "monetary_reset",
                score_walcl(walcl_yoy),
                0.18,
                "higher adds liquidity; sustained runoff keeps pressure on credit",
                "FRED WALCL",
                walcl_latest[0].isoformat(),
                f"Fed assets are {format_pct(walcl_yoy)} YoY at {format_billions(walcl_billions)}.",
                freshness=fred_freshness("WALCL", walcl_latest[0], as_of),
                age_days=signal_age_days(walcl_latest[0], as_of),
                value_label=format_pct(walcl_yoy),
            )
        )
    else:
        signals.append(
            missing_monetary_signal(
                "Fed Balance Sheet YoY",
                "WALCL",
                as_of,
                "Fed balance sheet YoY is unavailable because FRED WALCL has no usable observations.",
            )
        )

    rrp_latest = latest(observations["RRPONTSYD"])
    if rrp_latest:
        signals.append(
            regime_signal(
                "Reverse Repo Buffer",
                "monetary_reset",
                score_rrp(rrp_latest[1]),
                0.22,
                "lower means less residual liquidity buffer",
                "FRED RRPONTSYD",
                rrp_latest[0].isoformat(),
                f"Overnight reverse repo is {format_billions(rrp_latest[1])}, a thin remaining buffer.",
                freshness=fred_freshness("RRPONTSYD", rrp_latest[0], as_of),
                age_days=signal_age_days(rrp_latest[0], as_of),
                value_label=format_billions(rrp_latest[1]),
            )
        )
    else:
        signals.append(
            missing_monetary_signal(
                "Reverse Repo Buffer",
                "RRPONTSYD",
                as_of,
                "Reverse repo buffer is unavailable because FRED RRPONTSYD has no usable observations.",
            )
        )

    tga_latest = latest(observations["WTREGEN"])
    if tga_latest:
        tga_billions = tga_latest[1] / 1000.0
        signals.append(
            regime_signal(
                "Treasury General Account",
                "monetary_reset",
                score_tga(tga_billions),
                0.22,
                "higher can drain private liquidity",
                "FRED WTREGEN",
                tga_latest[0].isoformat(),
                f"TGA is {format_billions(tga_billions)}, which keeps liquidity conditions tighter.",
                freshness=fred_freshness("WTREGEN", tga_latest[0], as_of),
                age_days=signal_age_days(tga_latest[0], as_of),
                value_label=format_billions(tga_billions),
            )
        )
    else:
        signals.append(
            missing_monetary_signal(
                "Treasury General Account",
                "WTREGEN",
                as_of,
                "Treasury General Account is unavailable because FRED WTREGEN has no usable observations.",
            )
        )

    if walcl_latest and rrp_latest and tga_latest:
        net_liquidity = net_liquidity_billions(walcl_latest[1], rrp_latest[1], tga_latest[1])
        latest_component_date = min(walcl_latest[0], rrp_latest[0], tga_latest[0])
        previous_target = latest_component_date - timedelta(days=NET_LIQUIDITY_LOOKBACK_DAYS)
        previous_walcl = closest_to_date(
            observations["WALCL"],
            previous_target,
            NET_LIQUIDITY_LOOKBACK_TOLERANCE_DAYS,
        )
        previous_rrp = closest_to_date(
            observations["RRPONTSYD"],
            previous_target,
            NET_LIQUIDITY_LOOKBACK_TOLERANCE_DAYS,
        )
        previous_tga = closest_to_date(
            observations["WTREGEN"],
            previous_target,
            NET_LIQUIDITY_LOOKBACK_TOLERANCE_DAYS,
        )

        change_pct: Optional[float] = None
        if previous_walcl and previous_rrp and previous_tga:
            previous_net_liquidity = net_liquidity_billions(previous_walcl[1], previous_rrp[1], previous_tga[1])
            if previous_net_liquidity != 0:
                change_pct = (net_liquidity - previous_net_liquidity) / previous_net_liquidity * 100.0

        net_score = score_net_liquidity(change_pct)
        net_weight = 0.10 if change_pct is not None else 0.0
        change_note = (
            f"roughly 90d change is {format_pct(change_pct)}."
            if change_pct is not None
            else "roughly 90d change is unavailable from comparable component observations."
        )
        signals.append(
            regime_signal(
                "Net Liquidity Proxy",
                "monetary_reset",
                net_score,
                net_weight,
                "higher supports risk appetite; lower supports stress",
                "FRED WALCL-RRPONTSYD-WTREGEN",
                latest_component_date.isoformat(),
                f"Simple WALCL minus RRP minus TGA proxy is {format_billions(net_liquidity)}; {change_note}",
                freshness=signal_freshness(
                    latest_component_date,
                    as_of,
                    min(series_freshness_days("WALCL"), series_freshness_days("RRPONTSYD"), series_freshness_days("WTREGEN")),
                ),
                age_days=signal_age_days(latest_component_date, as_of),
                value_label=format_pct(change_pct),
            )
        )
    else:
        signals.append(
            missing_monetary_signal(
                "Net Liquidity Proxy",
                "WALCL-RRPONTSYD-WTREGEN",
                as_of,
                "Net liquidity proxy is unavailable because WALCL, RRPONTSYD, or WTREGEN is missing.",
            )
        )

    world_credit_observations = observations["Q5ACAMUSDA"]
    world_credit_latest = latest(world_credit_observations)
    world_credit_growth = three_year_annualized_growth_pct(world_credit_observations)
    if world_credit_latest is None:
        signals.append(
            missing_monetary_signal(
                "World Credit Growth",
                "Q5ACAMUSDA",
                as_of,
                "World credit growth is unavailable because FRED Q5ACAMUSDA has no usable observations.",
            )
        )
    else:
        signals.append(
            regime_signal(
                "World Credit Growth",
                "monetary_reset",
                score_credit_growth(world_credit_growth),
                0.28,
                "higher means the global stock of paper claims is expanding",
                "FRED Q5ACAMUSDA",
                world_credit_latest[0].isoformat(),
                (
                    "Total reporting countries credit to the non-financial sector is "
                    f"{format_pct(world_credit_growth)} 3Y annualized at {world_credit_latest[1]:,.1f}; "
                    "this is the core monetary reset signal, with U.S. M2 demoted to context."
                ),
                freshness=fred_freshness("Q5ACAMUSDA", world_credit_latest[0], as_of),
                age_days=signal_age_days(world_credit_latest[0], as_of),
                value_label=f"{format_pct(world_credit_growth)} 3Y annualized",
            )
        )
    add_growth_signal(
        signals,
        observations,
        "QUSCAMUSDA",
        "U.S. Credit Growth",
        0.08,
        score_credit_growth,
        "higher can signal domestic credit expansion and monetary stress",
        "U.S. total credit to the non-financial sector",
        as_of,
    )
    add_growth_signal(
        signals,
        observations,
        "GFDEBTN",
        "Federal Debt Growth",
        0.12,
        score_debt_growth,
        "higher means fiscal debt load is compounding faster",
        "Federal debt",
        as_of,
    )
    add_growth_signal(
        signals,
        observations,
        "A091RC1Q027SBEA",
        "Federal Interest Payments Growth",
        0.12,
        score_interest_growth,
        "higher means fiscal interest expense pressure is rising",
        "Federal government interest payments",
        as_of,
    )
    add_level_signal(
        signals,
        observations,
        "DGS10",
        "10Y Treasury Yield",
        0.04,
        score_treasury_yield,
        "higher yields tighten discount rates and debt-service conditions",
        "10Y Treasury yield is {value:.2f}%.",
        as_of,
    )
    add_level_signal(
        signals,
        observations,
        "DFII10",
        "10Y Real Yield",
        0.10,
        score_real_yield,
        "higher real yields increase monetary and credit pressure",
        "10Y real yield is {value:.2f}%.",
        as_of,
    )
    add_level_signal(
        signals,
        observations,
        "T10YIE",
        "10Y Breakeven Inflation",
        0.04,
        score_breakeven,
        "higher breakevens can signal inflation repricing pressure",
        "10Y breakeven inflation is {value:.2f}%.",
        as_of,
    )
    add_level_signal(
        signals,
        observations,
        "BAMLH0A0HYM2",
        "High-Yield Option-Adjusted Spread",
        0.12,
        score_high_yield_spread,
        "higher spreads mean credit stress is rising",
        "High-yield option-adjusted spread is {value:.2f} percentage points.",
        as_of,
    )

    return signals, newest_date


def weighted_monetary_signals(signals: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    weighted: List[Dict[str, Any]] = []
    for signal in signals:
        try:
            weight = float(signal.get("weight", 0))
        except (TypeError, ValueError):
            continue
        if signal.get("regime") == "monetary_reset" and weight > 0:
            weighted.append(signal)
    return weighted


def weighted_hard_money_signals(signals: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    weighted: List[Dict[str, Any]] = []
    for signal in signals:
        try:
            weight = float(signal.get("weight", 0))
        except (TypeError, ValueError):
            continue
        if signal.get("regime") == "hard_money_repricing" and weight > 0 and signal.get("freshness") != "future":
            weighted.append(signal)
    return weighted


def freshness_from_monetary_signals(signals: Iterable[Dict[str, Any]]) -> str:
    signal_list = list(signals)
    weighted = weighted_monetary_signals(signal_list)
    if not weighted:
        return "demo"

    if any(signal.get("freshness") == "missing" for signal in signal_list if signal.get("regime") == "monetary_reset"):
        return "stale"
    if any(signal.get("freshness") == "stale" for signal in weighted):
        return "stale"
    return "local_cache"


def freshness_from_hard_money_signals(signals: Iterable[Dict[str, Any]]) -> str:
    signal_list = list(signals)
    weighted = weighted_hard_money_signals(signal_list)
    if not weighted:
        return "demo"
    if any(signal.get("freshness") in ("stale", "missing", "future") for signal in signal_list if signal.get("regime") == "hard_money_repricing"):
        return "stale"
    return "local_cache"


def combined_freshness(monetary_freshness: str, hard_money_freshness: str) -> str:
    if monetary_freshness == "demo" and hard_money_freshness == "demo":
        return "demo"
    if monetary_freshness == "local_cache" and hard_money_freshness == "local_cache":
        return "local_cache"
    return "stale"


def cache_source(monetary_freshness: str, hard_money_freshness: str) -> str:
    if monetary_freshness != "demo" and hard_money_freshness != "demo":
        return "local_fred_and_market_cache"
    if monetary_freshness != "demo":
        return "local_fred_cache"
    if hard_money_freshness != "demo":
        return "local_market_cache"
    return "starter_static_cache"


def confidence_from_freshness(freshness: str, signal_count: int) -> str:
    if freshness == "local_cache" and signal_count >= 3:
        return "medium"
    return "low"


def choose_posture(scores: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    ai_productivity = scores["ai_productivity"]["score"]
    bubble = scores["ai_bubble_risk"]["score"]
    monetary = scores["monetary_reset"]["score"]
    energy = scores["energy_bottleneck"]["score"]
    hard_money = scores["hard_money_repricing"]["score"]

    if bubble >= 70 and monetary >= 65:
        return {
            "key": "defensive_barbell",
            "label": "Defensive Barbell",
            "explanation": "Bubble and monetary stress signals are both elevated; keep the barbell but demand more proof from AI capex returns.",
        }
    if ai_productivity >= 68 and monetary < 55 and bubble < 60:
        return {
            "key": "ai_growth_tilt",
            "label": "AI Growth Tilt",
            "explanation": "Productivity evidence is leading while monetary stress is contained; AIM-5 can lean toward productive AI exposure.",
        }
    if monetary >= 70 or energy >= 72 or hard_money >= 70:
        return {
            "key": "hard_money_barbell",
            "label": "Hard-Money Barbell",
            "explanation": "Monetary, hard-money repricing, or power bottleneck stress is elevated; keep BTC/gold and real-asset hedges central.",
        }
    return {
        "key": "barbell",
        "label": "Barbell",
        "explanation": "Base case: AI boom and monetary stress coexist; keep AIM-5 balance.",
    }


def build_cache(as_of: date) -> Dict[str, Any]:
    generated_at = utc_day_stamp(as_of)
    fred_cache, fred_error = read_json(FRED_CACHE)
    market_cache, market_error = read_json(MARKET_CACHE)

    monetary_signals: List[Dict[str, Any]] = []
    newest_date: Optional[date] = None
    if fred_cache is not None:
        monetary_signals, newest_date = build_monetary_signals(fred_cache, as_of)
    elif fred_error:
        monetary_signals = [
            missing_monetary_signal(
                "FRED Macro Cache",
                "fred-cache.json",
                as_of,
                f"No usable FRED cache ({fred_error}); monetary reset score uses a low-confidence starter fallback.",
            )
        ]

    hard_money_signals = build_hard_money_signals(market_cache, as_of, market_error)

    weighted_monetary = weighted_monetary_signals(monetary_signals)
    weighted_hard_money = weighted_hard_money_signals(hard_money_signals)
    monetary_freshness = freshness_from_monetary_signals(monetary_signals)
    hard_money_freshness = freshness_from_hard_money_signals(hard_money_signals)
    freshness = combined_freshness(monetary_freshness, hard_money_freshness)
    source = cache_source(monetary_freshness, hard_money_freshness)
    monetary_confidence = confidence_from_freshness(monetary_freshness, len(weighted_monetary))
    hard_money_confidence = confidence_from_freshness(hard_money_freshness, len(weighted_hard_money))
    monetary_score = weighted_score(weighted_monetary)
    hard_money_score = weighted_score(weighted_hard_money, fallback=50)

    if not weighted_monetary and fred_error:
        monetary_note = f"No usable FRED cache ({fred_error}); using a low-confidence starter score."
    elif not weighted_monetary:
        monetary_note = "No useful local FRED observations are available; using a low-confidence starter score."
    elif monetary_freshness == "stale":
        latest_label = newest_date.isoformat() if newest_date else "unknown"
        monetary_note = (
            f"One or more required FRED macro/credit signals are missing or stale as of {as_of.isoformat()} "
            f"(latest monetary observation {latest_label}); score is directional only."
        )
    else:
        monetary_note = "Local FRED liquidity, credit, fiscal, rates, and spread proxies are available; score is still a simple first-pass model."

    if not weighted_hard_money and market_error:
        hard_money_note = f"No usable market cache ({market_error}); hard-money repricing stays low-confidence."
    elif not weighted_hard_money:
        hard_money_note = "No useful BTC/gold market observations are available; hard-money repricing stays low-confidence."
    elif hard_money_freshness == "stale":
        hard_money_note = (
            f"BTC/gold market cache is present but missing or older than "
            f"{MARKET_FRESHNESS_MAX_AGE_DAYS} days as of {as_of.isoformat()}; score is directional only."
        )
    else:
        hard_money_note = "Local BTC and PAXG gold-proxy prices are available; hard-money repricing score is medium-confidence and simple."

    scores = {
        "ai_productivity": {
            "score": 55,
            "confidence": "low",
            "interpretation": "Evidence is real but monetization and broad productivity flow-through need more proof.",
        },
        "ai_bubble_risk": {
            "score": 50,
            "confidence": "low",
            "interpretation": "Capex is large; returns, utilization, and financing quality are not yet fully proven.",
        },
        "monetary_reset": {
            "score": monetary_score,
            "confidence": monetary_confidence,
            "interpretation": monetary_note,
        },
        "energy_bottleneck": {
            "score": 60,
            "confidence": "low",
            "interpretation": "Starter qualitative score: AI and Bitcoin both expose power and grid constraints.",
        },
        "hard_money_repricing": {
            "score": hard_money_score,
            "confidence": hard_money_confidence,
            "interpretation": hard_money_note,
        },
    }

    signals = starter_signals(generated_at) + hard_money_signals + monetary_signals
    dashboard_signals = dashboard_signal_watchlist({"signals": signals})

    return {
        "schema_version": SCHEMA_VERSION,
        "scoring_version": SCORING_VERSION,
        "generated_at": generated_at,
        "source": source,
        "freshness": freshness,
        "posture": choose_posture(scores),
        "scores": scores,
        "aim5_allocation": AIM5_ALLOCATION,
        "signals": signals,
        "dashboard_signals": dashboard_signals,
        "debate_question": DEBATE_QUESTION,
    }


def write_cache(cache: Dict[str, Any], path: Path) -> None:
    text = json.dumps(cache, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")


def parse_as_of(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--as-of must use YYYY-MM-DD") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the local AIM Macro Cockpit cache.")
    parser.add_argument(
        "--as-of",
        type=parse_as_of,
        default=None,
        help=(
            "UTC as-of date for deterministic cache generation. "
            "Use YYYY-MM-DD; generated_at will be YYYY-MM-DDT00:00:00Z."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    as_of = args.as_of or default_as_of()
    cache = build_cache(as_of)
    write_cache(cache, AIM_CACHE)

    scores = cache["scores"]
    print(
        "Wrote aim-cache.json | "
        f"posture={cache['posture']['label']} | "
        f"freshness={cache['freshness']} | "
        f"monetary_reset={scores['monetary_reset']['score']} "
        f"({scores['monetary_reset']['confidence']}) | "
        f"hard_money={scores['hard_money_repricing']['score']} "
        f"({scores['hard_money_repricing']['confidence']}) | "
        f"signals={len(cache['signals'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
