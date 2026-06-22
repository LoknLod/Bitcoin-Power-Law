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
AI_SIGNALS_CACHE = ROOT / "ai-signals-cache.json"
ENERGY_CACHE = ROOT / "energy-cache.json"
AIM_CACHE = ROOT / "aim-cache.json"
SCHEMA_VERSION = "aim_macro_cache.v0.1"
SCORING_VERSION = "aim_macro_scoring.v0.4"
FRESHNESS_MAX_AGE_DAYS = 45
QUARTERLY_FRESHNESS_MAX_AGE_DAYS = 370
ENERGY_FRESHNESS_MAX_AGE_DAYS = 120
MARKET_FRESHNESS_MAX_AGE_DAYS = 3
DIRECTIONAL_STALENESS_WARNING_DAYS = 90
NET_LIQUIDITY_LOOKBACK_DAYS = 90
NET_LIQUIDITY_LOOKBACK_TOLERANCE_DAYS = 14
BTC_GENESIS = date(2009, 1, 3)
BTC_POWER_LAW_A = -17.01
BTC_POWER_LAW_B = 5.82
BTC_HISTORY_EXTENDED = ROOT / "btc-history-extended.json"
WEEK_200_DAYS = 1400
POWER_LAW_GAP_THRESHOLD = -40.0
POWER_LAW_GAP_DECAY_DAYS = 90  # confidence drops to "low" after this many consecutive days below threshold
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
        "target_pct": 46,
        "role": "Semiconductors, AI platform, and test infrastructure bottlenecks.",
    },
    {
        "sleeve": "Agent Toll Collectors",
        "target_pct": 11,
        "role": "Toll collectors every agent needs: payments, trust, proprietary data.",
    },
    {
        "sleeve": "Energy / Power / Real Assets",
        "target_pct": 9.5,
        "role": "Power generation and grid bottleneck behind AI electrification.",
    },
    {
        "sleeve": "Hard Money / Monetary Reset Hedge",
        "target_pct": 7.5,
        "role": "Gold hedge. BTC lives outside per v0.4 doctrine.",
    },
    {
        "sleeve": "Healthcare / Defense",
        "target_pct": 13,
        "role": "Secular growth and geopolitical hedge.",
    },
    {
        "sleeve": "Critical Minerals",
        "target_pct": 6,
        "role": "Rare-earth optionality for electrification and semiconductor supply.",
    },
    {
        "sleeve": "Runway / Dry Powder",
        "target_pct": 7,
        "role": "T-bills for drawdown ammunition and deployment triggers.",
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
    if len(text) == 7 and text[4] == "-":
        text = f"{text}-01"
    elif len(text) >= 10:
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


def safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def signal_age_days(observed_at: date, as_of: date) -> int:
    return (as_of - observed_at).days


def signal_freshness(observed_at: date, as_of: date, max_age_days: int = FRESHNESS_MAX_AGE_DAYS) -> str:
    age_days = signal_age_days(observed_at, as_of)
    if age_days < 0:
        return "future"
    return "local_cache" if age_days <= max_age_days else "stale"


def fred_staleness_warning(observed_at: date, as_of: date) -> Optional[str]:
    age_days = signal_age_days(observed_at, as_of)
    if age_days <= DIRECTIONAL_STALENESS_WARNING_DAYS:
        return None
    return (
        f"FRED observation is {age_days} days old as of {as_of.isoformat()}; "
        "treat this score as directional only until the series refreshes."
    )


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


def score_btc_200wma(pct_vs_200w: Optional[float]) -> int:
    """Score the 200-week MA signal. Below = oversold/bullish for hard money."""
    if pct_vs_200w is None:
        return 50
    if pct_vs_200w <= -20.0:
        return 75  # deeply oversold — strong hard-money signal
    if pct_vs_200w <= -10.0:
        return 68
    if pct_vs_200w <= 0.0:
        return 62  # below 200wMA — oversold
    if pct_vs_200w <= 10.0:
        return 55  # near the line — neutral
    if pct_vs_200w <= 30.0:
        return 50  # above — normal
    return 45  # far above — potentially overextended


def compute_200wma(history: List[Dict[str, Any]]) -> Tuple[Optional[float], Optional[str]]:
    """Compute the 200-week (1400-day) moving average from BTC daily history."""
    if not isinstance(history, list) or len(history) < WEEK_200_DAYS:
        return None, f"insufficient history: {len(history) if isinstance(history, list) else 0} days, need {WEEK_200_DAYS}"
    closes = []
    for row in history[-WEEK_200_DAYS:]:
        price = safe_float(row.get("btc_usd"))
        if price is not None and price > 0:
            closes.append(price)
    if len(closes) < WEEK_200_DAYS * 0.95:  # allow 5% missing
        return None, f"too many missing closes: {WEEK_200_DAYS - len(closes)}"
    return sum(closes) / len(closes), None


def power_law_gap_duration_days(history: List[Dict[str, Any]], as_of: date) -> Optional[int]:
    """Count consecutive days the power-law gap has been below the threshold."""
    if not isinstance(history, list) or not history:
        return None
    consecutive = 0
    for row in reversed(history):
        observed_at = parse_cache_date(row.get("date"))
        btc_price = safe_float(row.get("btc_usd"))
        if observed_at is None or btc_price is None:
            continue
        fair_value = btc_power_law_fair_value(observed_at)
        if fair_value is None or fair_value <= 0:
            break
        gap_pct = (btc_price - fair_value) / fair_value * 100.0
        if gap_pct < POWER_LAW_GAP_THRESHOLD:
            consecutive += 1
        else:
            break
    return consecutive


def load_btc_history_extended() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    cache, error = read_json(BTC_HISTORY_EXTENDED)
    if error:
        return None, error
    series = cache.get("series") if isinstance(cache, dict) else None
    if not isinstance(series, list) or not series:
        return None, "btc-history-extended.json missing series array"
    return cache, None


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
    placeholder: bool = False,
    staleness_warning: Optional[str] = None,
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
    if placeholder:
        signal["placeholder"] = True
    if staleness_warning is not None:
        signal["staleness_warning"] = staleness_warning
    return signal


def energy_starter_signal(generated_at: str) -> Dict[str, Any]:
    as_of = generated_at[:10]
    return regime_signal(
        "Energy Bottleneck Starter",
        "energy_bottleneck",
        60,
        0.30,
        "higher means power constraints matter more",
        "starter_static_assumption",
        as_of,
        "Qualitative starter: AI data centers and Bitcoin mining both expose power constraints.",
        freshness="starter",
        placeholder=True,
    )


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
            placeholder=True,
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
            placeholder=True,
        ),
        energy_starter_signal(generated_at),
    ]


def parse_energy_observations(
    energy_cache: Dict[str, Any],
    series_id: str,
    field: str,
    through: Optional[date] = None,
) -> List[Observation]:
    series = energy_cache.get("series", {})
    if not isinstance(series, dict):
        return []
    payload = series.get(series_id)
    if not isinstance(payload, dict):
        return []
    raw_observations = payload.get("observations", [])
    if not isinstance(raw_observations, list):
        return []

    observations: List[Observation] = []
    for item in raw_observations:
        if not isinstance(item, dict):
            continue
        observed_at = parse_cache_date(item.get("date"))
        value = safe_float(item.get(field))
        if observed_at is None or value is None:
            continue
        if through is not None and observed_at > through:
            continue
        observations.append((observed_at, value))
    return sorted(observations, key=lambda row: row[0])


def energy_growth(observations: List[Observation]) -> Optional[float]:
    current = latest(observations)
    if current is None:
        return None
    previous = closest_to_date(observations, current[0] - timedelta(days=365), 45)
    if previous is None or previous[1] == 0:
        return None
    return (current[1] - previous[1]) / previous[1] * 100.0


def energy_signal_summary(signal: Dict[str, Any]) -> Dict[str, Any]:
    if signal.get("freshness") != "local_cache":
        return {
            "score": 60,
            "confidence": "low",
            "interpretation": "Starter qualitative score: AI and Bitcoin both expose power and grid constraints.",
        }
    return {
        "score": clamp_score(safe_float(signal.get("score")) or 60),
        "confidence": "medium",
        "interpretation": str(signal.get("note") or "EIA electricity data is available; score remains first-pass directional."),
    }


def build_energy_signal(energy_cache: Optional[Dict[str, Any]], generated_at: str, as_of: date) -> Dict[str, Any]:
    if not isinstance(energy_cache, dict) or energy_cache.get("schema_version") != "eia_energy_cache.v0.1":
        return energy_starter_signal(generated_at)

    commercial_price_all = parse_energy_observations(energy_cache, "commercial_electricity", "price_cents_per_kwh")
    commercial_sales_all = parse_energy_observations(energy_cache, "commercial_electricity", "sales_million_kwh")
    industrial_price_all = parse_energy_observations(energy_cache, "industrial_electricity", "price_cents_per_kwh")
    all_latest_dates = [latest(obs)[0] for obs in (commercial_price_all, commercial_sales_all, industrial_price_all) if latest(obs)]

    commercial_price = parse_energy_observations(energy_cache, "commercial_electricity", "price_cents_per_kwh", through=as_of)
    commercial_sales = parse_energy_observations(energy_cache, "commercial_electricity", "sales_million_kwh", through=as_of)
    industrial_price = parse_energy_observations(energy_cache, "industrial_electricity", "price_cents_per_kwh", through=as_of)
    latest_dates = [latest(obs)[0] for obs in (commercial_price, commercial_sales, industrial_price) if latest(obs)]
    if not latest_dates:
        if all_latest_dates and max(all_latest_dates) > as_of:
            observed_at = max(all_latest_dates)
            return regime_signal(
                "Energy Bottleneck Score",
                "energy_bottleneck",
                50,
                0.0,
                "future-dated EIA data is not scored",
                "eia_energy_cache",
                observed_at.isoformat(),
                "EIA energy cache contains observations after the AIM as-of date; excluded from scoring.",
                freshness="future",
                age_days=signal_age_days(observed_at, as_of),
            )
        return energy_starter_signal(generated_at)

    observed_at = max(latest_dates)
    freshness = signal_freshness(observed_at, as_of, ENERGY_FRESHNESS_MAX_AGE_DAYS)
    age_days = signal_age_days(observed_at, as_of)
    if freshness == "future":
        return regime_signal(
            "Energy Bottleneck Score",
            "energy_bottleneck",
            50,
            0.0,
            "future-dated EIA data is not scored",
            "eia_energy_cache",
            observed_at.isoformat(),
            "EIA energy cache contains observations after the AIM as-of date; excluded from scoring.",
            freshness="future",
            age_days=age_days,
        )
    if freshness != "local_cache":
        return energy_starter_signal(generated_at)

    def fresh_component(observations: List[Observation]) -> List[Observation]:
        current = latest(observations)
        if current is None:
            return []
        if signal_freshness(current[0], as_of, ENERGY_FRESHNESS_MAX_AGE_DAYS) != "local_cache":
            return []
        return observations

    commercial_price = fresh_component(commercial_price)
    commercial_sales = fresh_component(commercial_sales)
    industrial_price = fresh_component(industrial_price)
    commercial_price_yoy = energy_growth(commercial_price)
    commercial_sales_yoy = energy_growth(commercial_sales)
    industrial_price_yoy = energy_growth(industrial_price)
    if commercial_price_yoy is None and commercial_sales_yoy is None and industrial_price_yoy is None:
        return energy_starter_signal(generated_at)

    score_value = 45.0
    if commercial_price_yoy is not None:
        score_value += commercial_price_yoy * 1.0
    if commercial_sales_yoy is not None:
        score_value += commercial_sales_yoy * 0.7
    if industrial_price_yoy is not None:
        score_value += industrial_price_yoy * 0.4
    score = clamp_score(score_value)
    parts = []
    if commercial_price_yoy is not None:
        parts.append(f"commercial electricity price {format_pct(commercial_price_yoy)} YoY")
    if commercial_sales_yoy is not None:
        parts.append(f"commercial electricity sales {format_pct(commercial_sales_yoy)} YoY")
    if industrial_price_yoy is not None:
        parts.append(f"industrial electricity price {format_pct(industrial_price_yoy)} YoY")
    return regime_signal(
        "Energy Bottleneck Score",
        "energy_bottleneck",
        score,
        0.30,
        "higher means power-cost/load pressure is rising",
        "eia_energy_cache",
        observed_at.isoformat(),
        "EIA retail electricity data: " + "; ".join(parts) + ". First-pass proxy for AI/data-center energy bottleneck pressure.",
        freshness="local_cache",
        age_days=age_days,
        value_label="; ".join(parts),
    )


def ai_cache_observed_at(ai_cache: Dict[str, Any]) -> Optional[date]:
    metrics = ai_cache.get("company_metrics")
    dates = []
    if isinstance(metrics, list):
        for metric in metrics:
            if isinstance(metric, dict):
                parsed = parse_cache_date(metric.get("as_of"))
                if parsed is not None:
                    dates.append(parsed)
    if dates:
        return max(dates)
    return parse_cache_date(ai_cache.get("input_generated_at")) or parse_cache_date(ai_cache.get("generated_at"))


def ai_cache_signal(
    payload: Dict[str, Any],
    regime: str,
    weight: float,
    observed_at: date,
    as_of: date,
) -> Optional[Dict[str, Any]]:
    raw_score = safe_float(payload.get("score", 50))
    if raw_score is None:
        return None
    score = clamp_score(raw_score)
    freshness = signal_freshness(observed_at, as_of, QUARTERLY_FRESHNESS_MAX_AGE_DAYS)
    age_days = signal_age_days(observed_at, as_of)
    leaders = payload.get("leaders") if isinstance(payload.get("leaders"), list) else []
    leader_note = f" Leaders: {', '.join(str(item) for item in leaders[:3])}." if leaders else ""
    return regime_signal(
        str(payload.get("name") or "AI Signal"),
        regime,
        score,
        weight,
        str(payload.get("direction") or "higher is meaningful"),
        str(payload.get("source") or "ai-signals-cache"),
        observed_at.isoformat(),
        f"{str(payload.get('note') or 'Derived from AI financial signal cache.')}{leader_note}",
        freshness=freshness,
        age_days=age_days,
        value_label=f"score {score}/100",
    )


def build_ai_signals(
    ai_cache: Optional[Dict[str, Any]],
    generated_at: str,
    as_of: date,
) -> List[Dict[str, Any]]:
    if not isinstance(ai_cache, dict):
        return starter_signals(generated_at)
    if ai_cache.get("schema_version") != "ai_signals_cache.v0.1":
        return starter_signals(generated_at)
    metadata = ai_cache.get("metadata") if isinstance(ai_cache.get("metadata"), dict) else {}
    company_count = safe_float(metadata.get("company_count"))
    if company_count is None or company_count < 1:
        return starter_signals(generated_at)
    observed_at = ai_cache_observed_at(ai_cache)
    if observed_at is None:
        return starter_signals(generated_at)
    if signal_freshness(observed_at, as_of, QUARTERLY_FRESHNESS_MAX_AGE_DAYS) != "local_cache":
        return starter_signals(generated_at)

    signals = ai_cache.get("signals")
    if not isinstance(signals, dict):
        return starter_signals(generated_at)

    productivity = signals.get("ai_productivity")
    bubble = signals.get("ai_capex_bubble_risk")
    if not isinstance(productivity, dict) or not isinstance(bubble, dict):
        return starter_signals(generated_at)

    productivity_signal = ai_cache_signal(productivity, "ai_productivity", 0.35, observed_at, as_of)
    bubble_signal = ai_cache_signal(bubble, "ai_bubble_risk", 0.35, observed_at, as_of)
    if productivity_signal is None or bubble_signal is None:
        return starter_signals(generated_at)

    energy_signal = starter_signals(generated_at)[2]
    return [productivity_signal, bubble_signal, energy_signal]


def ai_score_summary(signals: List[Dict[str, Any]], regime: str, fallback_score: int, fallback_note: str) -> Dict[str, Any]:
    signal = next((item for item in signals if item.get("regime") == regime), None)
    if not isinstance(signal, dict) or signal.get("freshness") == "starter":
        return {"score": fallback_score, "confidence": "low", "interpretation": fallback_note}
    confidence = "medium" if signal.get("freshness") == "local_cache" else "low"
    score = safe_float(signal.get("score"))
    return {
        "score": clamp_score(score if score is not None else fallback_score),
        "confidence": confidence,
        "interpretation": str(signal.get("note") or fallback_note),
    }


DASHBOARD_SIGNAL_ORDER = [
    ("AI Productivity Score", "AI Productivity Starter"),
    ("AI Capex Bubble Risk", "AI Capex Bubble Starter"),
    ("BTC Power Law Fair Value Gap",),
    ("BTC/Gold Ratio",),
    ("World Credit Growth",),
]


def dashboard_signal_watchlist(cache: Dict[str, Any]) -> List[Dict[str, Any]]:
    signals = cache.get("signals", []) if isinstance(cache, dict) else []
    if not isinstance(signals, list):
        return []
    by_name = {signal.get("name"): signal for signal in signals if isinstance(signal, dict)}
    watchlist = []
    for slot in DASHBOARD_SIGNAL_ORDER:
        for name in slot:
            if name in by_name:
                watchlist.append(by_name[name])
                break
    return watchlist


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

    # Load extended BTC history for 200-week MA and gap duration
    btc_history_cache, _ = load_btc_history_extended()
    btc_history = btc_history_cache.get("series") if btc_history_cache else None
    ma_200w, ma_200w_error = compute_200wma(btc_history) if btc_history else (None, "no btc-history-extended.json")
    gap_duration = power_law_gap_duration_days(btc_history, as_of) if btc_history else None

    signals: List[Dict[str, Any]] = []

    # --- 200-week MA signal (primary, model-free) ---
    if btc_price is not None and ma_200w is not None:
        pct_vs_200w = (btc_price / ma_200w - 1.0) * 100.0
        freshness, age_days = market_freshness(btc_date, as_of)
        signals.append(
            regime_signal(
                "BTC 200-Week Moving Average",
                "hard_money_repricing",
                score_btc_200wma(pct_vs_200w),
                0.35,
                "below 200wMA = oversold (bullish for hard money); above = normal",
                btc_source,
                btc_date.isoformat() if btc_date else as_of.isoformat(),
                (
                    f"BTC spot is {format_usd_price(btc_price)} versus a 200-week MA of "
                    f"{format_usd_price(ma_200w)}, a {format_pct(pct_vs_200w)} deviation. "
                    f"Model-free long-term trend signal."
                ),
                freshness=freshness,
                age_days=age_days,
                value_label=format_pct(pct_vs_200w),
            )
        )
    else:
        reason = "BTC spot unavailable" if btc_price is None else f"200-week MA unavailable: {ma_200w_error}"
        signals.append(missing_hard_money_signal("BTC 200-Week Moving Average", as_of, reason or missing_reason))

    # --- Power law gap signal (secondary, reduced weight) ---
    if btc_price is not None and fair_value is not None:
        gap_pct = (btc_price - fair_value) / fair_value * 100.0
        freshness, age_days = market_freshness(btc_date, as_of)
        gap_note = (
            f"BTC spot is {format_usd_price(btc_price)} versus a power-law anchor of "
            f"{format_usd_price(fair_value)}, a {format_pct(gap_pct)} gap."
        )
        if gap_duration is not None and gap_duration > 0:
            gap_note += f" Gap has been below {POWER_LAW_GAP_THRESHOLD:.0f}% for {gap_duration} consecutive days."
            if gap_duration >= POWER_LAW_GAP_DECAY_DAYS:
                gap_note += " Confidence decayed to low (extended gap without reversion)."
        signals.append(
            regime_signal(
                "BTC Power Law Fair Value Gap",
                "hard_money_repricing",
                score_btc_power_gap(gap_pct),
                0.25,  # reduced from 0.45 — 200-week MA is now primary
                "higher means BTC is repricing above the time-based power-law anchor",
                btc_source,
                btc_date.isoformat() if btc_date else as_of.isoformat(),
                gap_note,
                freshness=freshness,
                age_days=age_days,
                value_label=format_pct(gap_pct),
            )
        )
    else:
        reason = "BTC spot price unavailable" if btc_price is None else "BTC power-law anchor unavailable before genesis"
        signals.append(missing_hard_money_signal("BTC Power Law Fair Value Gap", as_of, reason or missing_reason))

    # --- BTC/Gold Ratio (unchanged weight) ---
    if btc_price is not None and gold_price is not None:
        ratio = btc_price / gold_price
        freshness, age_days, observed_at = combined_market_freshness((btc_date, gold_date), as_of)
        signals.append(
            regime_signal(
                "BTC/Gold Ratio",
                "hard_money_repricing",
                score_btc_gold_ratio(ratio),
                0.25,
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

    # --- Gold proxy price (unchanged) ---
    if gold_price is not None:
        freshness, age_days = market_freshness(gold_date, as_of)
        signals.append(
            regime_signal(
                "Gold Proxy Price",
                "hard_money_repricing",
                score_gold_price(gold_price),
                0.15,  # reduced from 0.20 — 200-week MA takes primary weight
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
            staleness_warning=fred_staleness_warning(series_latest[0], as_of),
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
            staleness_warning=fred_staleness_warning(series_latest[0], as_of),
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
                staleness_warning=fred_staleness_warning(wm2_latest[0], as_of),
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
                staleness_warning=fred_staleness_warning(walcl_latest[0], as_of),
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
                staleness_warning=fred_staleness_warning(rrp_latest[0], as_of),
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
                staleness_warning=fred_staleness_warning(tga_latest[0], as_of),
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
                staleness_warning=fred_staleness_warning(latest_component_date, as_of),
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
                staleness_warning=fred_staleness_warning(world_credit_latest[0], as_of),
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


def stale_monetary_input_signals(signals: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    stale: List[Dict[str, Any]] = []
    for signal in weighted_monetary_signals(signals):
        source = str(signal.get("source") or "")
        if not source.startswith("FRED"):
            continue
        age_days = safe_float(signal.get("age_days"))
        if age_days is None:
            continue
        if age_days > DIRECTIONAL_STALENESS_WARNING_DAYS:
            stale.append(signal)
    return stale


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


def build_cache(as_of: date, ai_signals_cache_path: Optional[Path] = None, energy_cache_path: Optional[Path] = None) -> Dict[str, Any]:
    generated_at = utc_day_stamp(as_of)
    fred_cache, fred_error = read_json(FRED_CACHE)
    market_cache, market_error = read_json(MARKET_CACHE)
    ai_cache = None
    if ai_signals_cache_path is not None:
        ai_cache, _ai_error = read_json(ai_signals_cache_path)
    energy_cache = None
    if energy_cache_path is not None:
        energy_cache, _energy_error = read_json(energy_cache_path)

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
    ai_signals = build_ai_signals(ai_cache, generated_at, as_of)
    energy_signal = build_energy_signal(energy_cache, generated_at, as_of)
    ai_signals = [signal for signal in ai_signals if signal.get("regime") != "energy_bottleneck"] + [energy_signal]

    weighted_monetary = weighted_monetary_signals(monetary_signals)
    weighted_hard_money = weighted_hard_money_signals(hard_money_signals)
    monetary_freshness = freshness_from_monetary_signals(monetary_signals)
    hard_money_freshness = freshness_from_hard_money_signals(hard_money_signals)
    freshness = combined_freshness(monetary_freshness, hard_money_freshness)
    source = cache_source(monetary_freshness, hard_money_freshness)
    stale_monetary_inputs = stale_monetary_input_signals(monetary_signals)
    monetary_confidence = confidence_from_freshness(monetary_freshness, len(weighted_monetary))
    hard_money_confidence = confidence_from_freshness(hard_money_freshness, len(weighted_hard_money))
    if stale_monetary_inputs:
        monetary_confidence = "low"

    # Power-law gap confidence decay: if the gap has been below threshold for an
    # extended period without reversion, downgrade hard-money confidence to "low"
    btc_history_cache_decay, _ = load_btc_history_extended()
    btc_history_decay = btc_history_cache_decay.get("series") if btc_history_cache_decay else None
    gap_duration = power_law_gap_duration_days(btc_history_decay, as_of) if btc_history_decay else None
    if gap_duration is not None and gap_duration >= POWER_LAW_GAP_DECAY_DAYS and hard_money_confidence == "medium":
        hard_money_confidence = "low"
    monetary_score = weighted_score(weighted_monetary)
    hard_money_score = weighted_score(weighted_hard_money, fallback=50)

    if not weighted_monetary and fred_error:
        monetary_note = f"No usable FRED cache ({fred_error}); using a low-confidence starter score."
    elif not weighted_monetary:
        monetary_note = "No useful local FRED observations are available; using a low-confidence starter score."
    elif stale_monetary_inputs:
        stale_names = ", ".join(str(signal.get("name")) for signal in stale_monetary_inputs[:4])
        oldest_age = max(int(safe_float(signal.get("age_days")) or 0) for signal in stale_monetary_inputs)
        monetary_note = (
            f"One or more weighted FRED macro/credit inputs are older than "
            f"{DIRECTIONAL_STALENESS_WARNING_DAYS} days as of {as_of.isoformat()} "
            f"({stale_names}; oldest {oldest_age} days); score is directional only."
        )
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
        hard_money_note = "Local BTC and PAXG gold-proxy prices are available; hard-money repricing score includes 200-week MA (primary), power-law gap (secondary), BTC/gold ratio, and gold proxy."

    scores = {
        "ai_productivity": ai_score_summary(
            ai_signals,
            "ai_productivity",
            55,
            "Evidence is real but monetization and broad productivity flow-through need more proof.",
        ),
        "ai_bubble_risk": ai_score_summary(
            ai_signals,
            "ai_bubble_risk",
            50,
            "Capex is large; returns, utilization, and financing quality are not yet fully proven.",
        ),
        "monetary_reset": {
            "score": monetary_score,
            "confidence": monetary_confidence,
            "interpretation": monetary_note,
        },
        "energy_bottleneck": energy_signal_summary(energy_signal),
        "hard_money_repricing": {
            "score": hard_money_score,
            "confidence": hard_money_confidence,
            "interpretation": hard_money_note,
        },
    }

    signals = ai_signals + hard_money_signals + monetary_signals
    dashboard_signals = dashboard_signal_watchlist({"signals": signals})

    return {
        "schema_version": SCHEMA_VERSION,
        "scoring_version": SCORING_VERSION,
        "aim_schema_version": SCHEMA_VERSION,
        "aim_scoring_version": SCORING_VERSION,
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
    parser.add_argument(
        "--ai-signals-cache",
        type=Path,
        default=None,
        help=(
            "Optional ai-signals-cache.json input. Deliberately opt-in so ignored local API caches "
            "cannot silently change the tracked aim-cache.json artifact."
        ),
    )
    parser.add_argument(
        "--energy-cache",
        type=Path,
        default=None,
        help=(
            "Optional energy-cache.json input. Deliberately opt-in so ignored local EIA API caches "
            "cannot silently change the tracked aim-cache.json artifact."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    as_of = args.as_of or default_as_of()
    cache = build_cache(as_of, ai_signals_cache_path=args.ai_signals_cache, energy_cache_path=args.energy_cache)
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
