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
AIM_CACHE = ROOT / "aim-cache.json"
SCHEMA_VERSION = "aim_macro_cache.v0.1"
SCORING_VERSION = "aim_macro_scoring.v0.1"
FRESHNESS_MAX_AGE_DAYS = 45
NET_LIQUIDITY_LOOKBACK_DAYS = 90
NET_LIQUIDITY_LOOKBACK_TOLERANCE_DAYS = 14
BTC_GENESIS = date(2009, 1, 3)
BTC_POWER_LAW_A = -17.01
BTC_POWER_LAW_B = 5.82
DEBATE_QUESTION = "Is AI producing real productivity faster than credit/fiscal stress is degrading money?"


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


def signal_age_days(observed_at: date, as_of: date) -> int:
    return max(0, (as_of - observed_at).days)


def signal_freshness(observed_at: date, as_of: date) -> str:
    return "local_cache" if signal_age_days(observed_at, as_of) <= FRESHNESS_MAX_AGE_DAYS else "stale"


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


def hard_money_signal(as_of: date) -> Dict[str, Any]:
    fair_value = btc_power_law_fair_value(as_of)
    fair_value_note = (
        f"BTC power-law fair value anchor is {format_usd_price(fair_value)} as of {as_of.isoformat()}."
        if fair_value is not None
        else "BTC power-law fair value anchor is unavailable before genesis."
    )
    return regime_signal(
        "BTC Power Law Fair Value Anchor",
        "hard_money_repricing",
        50,
        0.0,
        "time-based anchor only; live price spread pending",
        "repo_formula",
        as_of.isoformat(),
        f"{fair_value_note} Live BTC price source pending; no local price spread is scored, so valuation repricing remains informational.",
        freshness="starter",
    )


def build_monetary_signals(cache: Dict[str, Any], as_of: date) -> Tuple[List[Dict[str, Any]], Optional[date]]:
    observations = {
        "WM2NS": parse_observations(cache, "WM2NS", through=as_of),
        "WALCL": parse_observations(cache, "WALCL", through=as_of),
        "RRPONTSYD": parse_observations(cache, "RRPONTSYD", through=as_of),
        "WTREGEN": parse_observations(cache, "WTREGEN", through=as_of),
    }

    latest_dates = [row[0] for rows in observations.values() for row in rows[-1:]]
    newest_date = max(latest_dates) if latest_dates else None
    signals: List[Dict[str, Any]] = []

    wm2_latest = latest(observations["WM2NS"])
    wm2_yoy = yoy_change_pct(observations["WM2NS"])
    if wm2_latest:
        signals.append(
            regime_signal(
                "M2 Money Stock YoY",
                "monetary_reset",
                score_m2(wm2_yoy),
                0.28,
                "higher can signal debasement pressure; contraction can signal credit stress",
                "FRED WM2NS",
                wm2_latest[0].isoformat(),
                f"US M2 is {format_pct(wm2_yoy)} YoY at {format_billions(wm2_latest[1])}.",
                freshness=signal_freshness(wm2_latest[0], as_of),
                age_days=signal_age_days(wm2_latest[0], as_of),
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
                freshness=signal_freshness(walcl_latest[0], as_of),
                age_days=signal_age_days(walcl_latest[0], as_of),
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
                freshness=signal_freshness(rrp_latest[0], as_of),
                age_days=signal_age_days(rrp_latest[0], as_of),
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
                freshness=signal_freshness(tga_latest[0], as_of),
                age_days=signal_age_days(tga_latest[0], as_of),
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
                freshness=signal_freshness(latest_component_date, as_of),
                age_days=signal_age_days(latest_component_date, as_of),
            )
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


def freshness_from_monetary_signals(signals: Iterable[Dict[str, Any]]) -> str:
    weighted = weighted_monetary_signals(signals)
    if not weighted:
        return "demo"

    if any(signal.get("freshness") == "stale" for signal in weighted):
        return "stale"
    return "local_cache"


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

    monetary_signals: List[Dict[str, Any]] = []
    newest_date: Optional[date] = None
    if fred_cache is not None:
        monetary_signals, newest_date = build_monetary_signals(fred_cache, as_of)

    weighted_monetary = weighted_monetary_signals(monetary_signals)
    freshness = freshness_from_monetary_signals(monetary_signals)
    source = "local_fred_cache" if weighted_monetary else "starter_static_cache"
    monetary_confidence = confidence_from_freshness(freshness, len(weighted_monetary))
    monetary_score = weighted_score(monetary_signals)

    if not weighted_monetary and fred_error:
        monetary_note = f"No usable FRED cache ({fred_error}); using a low-confidence starter score."
    elif not weighted_monetary:
        monetary_note = "No useful local FRED observations are available; using a low-confidence starter score."
    elif freshness == "stale":
        latest_label = newest_date.isoformat() if newest_date else "unknown"
        monetary_note = (
            f"One or more required weighted FRED signals are stale beyond "
            f"{FRESHNESS_MAX_AGE_DAYS} days as of {as_of.isoformat()} "
            f"(latest monetary observation {latest_label}); score is directional only."
        )
    else:
        monetary_note = "Local FRED liquidity proxies are available; score is still a simple first-pass model."

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
            "score": 50,
            "confidence": "low",
            "interpretation": "BTC power-law fair value is included as a time-based anchor; local live BTC price spread scoring is pending.",
        },
    }

    signals = starter_signals(generated_at) + [hard_money_signal(as_of)] + monetary_signals

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
        f"signals={len(cache['signals'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
