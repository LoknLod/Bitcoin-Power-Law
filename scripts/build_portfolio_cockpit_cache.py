#!/usr/bin/env python3
"""Build Shrike's private portfolio cockpit overlay.

This script intentionally writes a separate local-only cache instead of baking
Doug's Schwab/Kubera values into the public AIM cache. The static dashboard can
fetch this file when it exists locally; public/demo deployments keep portfolio
metrics suppressed.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORTFOLIO_STATE = Path("/Volumes/ShrikeAI/Shrike/Portfolio/state/portfolio_state.json")
DEFAULT_PRIVATE_PLAN = Path("/Volumes/ShrikeAI/Shrike/Portfolio/state/retirement_plan_private.json")
DEFAULT_AIM_CACHE = ROOT / "aim-cache.json"
DEFAULT_OUTPUT = ROOT / "portfolio-cockpit-cache.json"
SCHEMA_VERSION = "shrike_portfolio_cockpit.v0.1"
REQUIRED_SCOPE = "schwab_employer_sponsored_visible_accounts_only"


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} root must be a JSON object")
    return data


def parse_as_of(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--as-of must use YYYY-MM-DD") from exc


def money(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = default
    return round(numeric, 2)


def pct(value: float) -> float:
    return round(value * 100.0, 1)


def validate_portfolio_state(portfolio_state: Dict[str, Any]) -> None:
    if portfolio_state.get("mutation_allowed") is not False:
        raise ValueError("portfolio_state must be read-only with mutation_allowed=false")
    if portfolio_state.get("scope") != REQUIRED_SCOPE:
        raise ValueError("portfolio_state must use top-level non-hidden Schwab account scope")
    if portfolio_state.get("source") != "kubera":
        raise ValueError("portfolio_state source must be kubera")
    if "total_value" not in portfolio_state:
        raise ValueError("portfolio_state missing total_value")


def account_rows(accounts: Any) -> List[Dict[str, Any]]:
    if not isinstance(accounts, list):
        return []
    rows: List[Dict[str, Any]] = []
    for account in accounts:
        if not isinstance(account, dict):
            continue
        provider = str(account.get("provider") or "unknown")
        if provider != "Charles Schwab":
            raise ValueError("portfolio_state contains a non-Schwab account row")
        rows.append(
            {
                "provider": provider,
                "value": money(account.get("value")),
                "holdings_count": int(account.get("holdings_count") or 0),
            }
        )
    return rows


def required_annual_growth(current: float, target: float, as_of: date, target_year: int) -> float:
    if current <= 0 or target <= current:
        return 0.0
    target_day = date(target_year, 10, 1)
    years = max((target_day - as_of).days / 365.25, 0.01)
    return round((pow(target / current, 1 / years) - 1) * 100.0, 1)


def btc_power_law_signal(aim_cache: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    signals = aim_cache.get("signals", [])
    if not isinstance(signals, list):
        return None
    for signal in signals:
        if isinstance(signal, dict) and signal.get("name") == "BTC Power Law Fair Value Gap":
            return {
                "name": signal.get("name"),
                "score": signal.get("score"),
                "value": signal.get("value"),
                "value_label": signal.get("value_label"),
                "as_of": signal.get("as_of"),
                "freshness": signal.get("freshness"),
            }
    return None


def readiness_label(progress: float) -> str:
    if progress >= 0.9:
        return "green"
    if progress >= 0.65:
        return "yellow"
    return "red"


def plan_value(private_plan: Dict[str, Any], key: str, override: Optional[float]) -> float:
    value = override if override is not None else private_plan.get(key)
    if value is None:
        raise ValueError(f"Missing private plan value: {key}. Provide --{key.replace('_', '-')} or a private plan file.")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Private plan value {key} must be numeric") from exc
    if numeric < 0:
        raise ValueError(f"Private plan value {key} must be non-negative")
    return numeric


def load_private_plan(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    data = read_json(path)
    if data.get("mutation_allowed") is not False:
        raise ValueError("private plan must be read-only with mutation_allowed=false")
    return data


def build_cache(
    portfolio_state: Dict[str, Any],
    aim_cache: Dict[str, Any],
    *,
    as_of: Optional[date] = None,
    btc_held: float,
    btc_target: float,
    retirement_target: float,
    pension_annual: float,
) -> Dict[str, Any]:
    validate_portfolio_state(portfolio_state)
    today = as_of or datetime.now(timezone.utc).date()
    declared_total = money(portfolio_state.get("total_value"))
    target = money(retirement_target)
    accounts = account_rows(portfolio_state.get("accounts"))
    computed_total = money(sum(row["value"] for row in accounts))
    if not accounts:
        raise ValueError("portfolio_state must include at least one Schwab account row")
    if abs(computed_total - declared_total) > 1.0:
        raise ValueError("portfolio_state total_value does not reconcile with Schwab account rows")
    schwab_total = computed_total
    progress = schwab_total / target if target > 0 else 0.0
    power_law = btc_power_law_signal(aim_cache)

    shrike_read = (
        f"Schwab retirement source is live from Kubera at ${schwab_total:,.0f}; "
        f"2030 optionality remains a gap-to-target problem while 2035 baseline is helped by the ${pension_annual:,.0f}/yr pension. "
        "Use this cockpit for posture and review, not trade execution."
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": f"{today.isoformat()}T00:00:00Z",
        "source": "kubera_schwab_plus_aim_cache",
        "freshness": portfolio_state.get("freshness", "unknown"),
        "mutation_allowed": False,
        "privacy_note": "private local overlay. Do not commit or publish: contains Doug's Schwab/Kubera retirement values.",
        "portfolio": {
            "scope": portfolio_state.get("scope"),
            "source_generated_at": portfolio_state.get("generated_at"),
            "schwab_total": schwab_total,
            "schwab_total_display": f"${schwab_total:,.2f}",
            "account_count": len(accounts),
            "notes": [
                "Uses top-level non-hidden Charles Schwab account rows from Kubera.",
                "Does not use Kubera netWorth or nested holdings, avoiding double-counting and non-Schwab contamination.",
            ],
        },
        "btc": {
            "held": round(float(btc_held), 4),
            "target": round(float(btc_target), 4),
            "target_gap": round(max(float(btc_target) - float(btc_held), 0.0), 4),
            "provenance": "private_plan",
            "as_of": today.isoformat(),
            "power_law_signal": power_law,
        },
        "retirement": {
            "target_portfolio": target,
            "target_portfolio_display": f"${target:,.0f}",
            "schwab_progress_pct": pct(progress),
            "schwab_gap_to_target": money(max(target - schwab_total, 0.0)),
            "required_annual_growth_to_2030": required_annual_growth(schwab_total, target, today, 2030),
            "required_annual_growth_to_2035": required_annual_growth(schwab_total, target, today, 2035),
            "pension_annual": money(pension_annual),
            "pension_annual_display": f"${pension_annual:,.0f}/yr",
            "early_2030_readiness": readiness_label(progress),
            "faa_2035_readiness": "yellow" if progress < 0.9 else "green",
        },
        "aim": {
            "posture": aim_cache.get("posture", {}),
            "hard_money_score": (aim_cache.get("scores", {}).get("hard_money_repricing", {}) if isinstance(aim_cache.get("scores"), dict) else {}).get("score"),
        },
        "action_posture": "Hold / Watch / Research only — no broker or account mutation.",
        "shrike_read": shrike_read,
    }


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Shrike's private portfolio cockpit overlay cache.")
    parser.add_argument("--portfolio-state", type=Path, default=DEFAULT_PORTFOLIO_STATE)
    parser.add_argument("--private-plan", type=Path, default=DEFAULT_PRIVATE_PLAN)
    parser.add_argument("--aim-cache", type=Path, default=DEFAULT_AIM_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--as-of", type=parse_as_of, default=None)
    parser.add_argument("--btc-held", type=float, default=None)
    parser.add_argument("--btc-target", type=float, default=None)
    parser.add_argument("--retirement-target", type=float, default=None)
    parser.add_argument("--pension-annual", type=float, default=None)
    return parser.parse_args(argv)


def run(argv: Optional[List[str]] = None) -> str:
    args = parse_args(argv)
    portfolio_state = read_json(args.portfolio_state)
    aim_cache = read_json(args.aim_cache)
    private_plan = load_private_plan(args.private_plan)
    cache = build_cache(
        portfolio_state,
        aim_cache,
        as_of=args.as_of,
        btc_held=plan_value(private_plan, "btc_held", args.btc_held),
        btc_target=plan_value(private_plan, "btc_target", args.btc_target),
        retirement_target=plan_value(private_plan, "retirement_target", args.retirement_target),
        pension_annual=plan_value(private_plan, "pension_annual", args.pension_annual),
    )
    args.output.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return f"Wrote {args.output.name} | source={cache['source']} | freshness={cache['freshness']} | private_overlay=true"


def main() -> int:
    print(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
