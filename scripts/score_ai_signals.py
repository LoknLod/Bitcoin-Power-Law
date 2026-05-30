#!/usr/bin/env python3
"""Score AIM AI productivity and capex-bubble signals from Alpha Vantage cache."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "alpha-vantage-cache.json"
DEFAULT_OUTPUT = ROOT / "ai-signals-cache.json"
SCHEMA_VERSION = "ai_signals_cache.v0.1"


def utc_now_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_number(value: Any) -> Optional[float]:
    if value in (None, "", ".", "None"):
        return None
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def pct(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator in (None, 0):
        return None
    return (numerator / denominator) * 100.0


def yoy_pct(current: Optional[float], prior: Optional[float]) -> Optional[float]:
    if current is None or prior in (None, 0):
        return None
    return ((current - prior) / abs(prior)) * 100.0


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def normalize_score(value: Optional[float], low: float, high: float) -> float:
    if value is None:
        return 50.0
    if high == low:
        return 50.0
    return clamp(((value - low) / (high - low)) * 100.0)


def sorted_reports(payload: Dict[str, Any], key: str = "quarterlyReports") -> List[Dict[str, Any]]:
    reports = payload.get(key)
    if not isinstance(reports, list):
        return []
    return sorted([r for r in reports if isinstance(r, dict)], key=lambda r: str(r.get("fiscalDateEnding") or ""), reverse=True)


def report_pair(payload: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    reports = sorted_reports(payload)
    if not reports:
        return None, None
    current = reports[0]
    current_date = str(current.get("fiscalDateEnding") or "")
    prior_year = current_date[:4]
    target_prefix = None
    if len(current_date) >= 4 and prior_year.isdigit():
        target_prefix = str(int(prior_year) - 1) + current_date[4:]
    prior = None
    if target_prefix:
        prior = next((r for r in reports[1:] if str(r.get("fiscalDateEnding") or "") == target_prefix), None)
    return current, prior


def field(report: Optional[Dict[str, Any]], *names: str) -> Optional[float]:
    if not report:
        return None
    for name in names:
        parsed = parse_number(report.get(name))
        if parsed is not None:
            return parsed
    return None


def positive_abs(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return abs(value)


def total_debt(report: Optional[Dict[str, Any]]) -> Optional[float]:
    total = field(report, "shortLongTermDebtTotal", "totalDebt")
    if total is not None:
        return total
    short = field(report, "shortTermDebt", "currentDebt") or 0.0
    long = field(report, "longTermDebt") or 0.0
    if short == 0.0 and long == 0.0:
        return None
    return short + long


def company_metrics(ticker: str, ticker_payload: Dict[str, Any]) -> Dict[str, Any]:
    income_now, income_prior = report_pair(ticker_payload.get("income_statement") or {})
    cash_now, cash_prior = report_pair(ticker_payload.get("cash_flow") or {})
    balance_now, balance_prior = report_pair(ticker_payload.get("balance_sheet") or {})

    revenue = field(income_now, "totalRevenue")
    revenue_prior = field(income_prior, "totalRevenue")
    operating_income = field(income_now, "operatingIncome")
    rnd = field(income_now, "researchAndDevelopment")
    operating_cashflow = field(cash_now, "operatingCashflow", "operatingCashFlow")
    capex = positive_abs(field(cash_now, "capitalExpenditures", "capitalExpenditure"))
    capex_prior = positive_abs(field(cash_prior, "capitalExpenditures", "capitalExpenditure"))
    depreciation = field(cash_now, "depreciationDepletionAndAmortization", "depreciation")
    depreciation_prior = field(cash_prior, "depreciationDepletionAndAmortization", "depreciation")
    debt = total_debt(balance_now)
    debt_prior = total_debt(balance_prior)

    fcf = None
    if operating_cashflow is not None and capex is not None:
        fcf = operating_cashflow - capex

    return {
        "ticker": ticker.upper(),
        "as_of": str((income_now or cash_now or balance_now or {}).get("fiscalDateEnding") or ""),
        "revenue": revenue,
        "revenue_yoy_pct": yoy_pct(revenue, revenue_prior),
        "capex": capex,
        "capex_yoy_pct": yoy_pct(capex, capex_prior),
        "capex_to_revenue_pct": pct(capex, revenue),
        "operating_margin_pct": pct(operating_income, revenue),
        "free_cash_flow": fcf,
        "free_cash_flow_margin_pct": pct(fcf, revenue),
        "debt": debt,
        "debt_yoy_pct": yoy_pct(debt, debt_prior),
        "depreciation_yoy_pct": yoy_pct(depreciation, depreciation_prior),
        "r_and_d_to_revenue_pct": pct(rnd, revenue),
    }


def average(values: Iterable[Optional[float]]) -> Optional[float]:
    valid = [value for value in values if value is not None and math.isfinite(value)]
    if not valid:
        return None
    return sum(valid) / len(valid)


def productivity_component(metrics: Dict[str, Any]) -> float:
    return average([
        normalize_score(metrics.get("revenue_yoy_pct"), -5, 30),
        normalize_score(metrics.get("operating_margin_pct"), 5, 35),
        normalize_score(metrics.get("free_cash_flow_margin_pct"), -10, 25),
        normalize_score(metrics.get("r_and_d_to_revenue_pct"), 3, 18),
    ]) or 50.0


def bubble_component(metrics: Dict[str, Any]) -> float:
    return average([
        normalize_score(metrics.get("capex_yoy_pct"), 0, 150),
        normalize_score(metrics.get("capex_to_revenue_pct"), 5, 40),
        100.0 - normalize_score(metrics.get("free_cash_flow_margin_pct"), -25, 20),
        normalize_score(metrics.get("debt_yoy_pct"), 0, 100),
        normalize_score(metrics.get("depreciation_yoy_pct"), 0, 120),
    ]) or 50.0


def rounded(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(value, 2)


def marker_count(markers: Dict[str, Any], name: str) -> float:
    parsed = parse_number(markers.get(name))
    return parsed or 0.0


def sec_language_scores(markers: Dict[str, Any]) -> Dict[str, float]:
    """Convert SEC filing-language marker counts into 0-100 evidence scores."""
    ai_mentions = marker_count(markers, "ai_mentions")
    monetization = marker_count(markers, "monetization_mentions")
    capex = marker_count(markers, "capex_infrastructure_mentions")
    energy = marker_count(markers, "energy_constraint_mentions")
    obligations = marker_count(markers, "obligation_risk_mentions")

    productivity = average([
        normalize_score(ai_mentions, 0, 10),
        normalize_score(monetization, 0, 8),
        100.0 - normalize_score(max(0.0, capex - monetization), 0, 12),
    ]) or 50.0
    bubble = average([
        normalize_score(capex, 0, 12),
        normalize_score(energy, 0, 6),
        normalize_score(obligations, 0, 6),
        100.0 - normalize_score(monetization, 0, 8),
    ]) or 50.0
    return {
        "sec_language_productivity_score": productivity,
        "sec_language_bubble_risk_score": bubble,
    }


def sec_language_by_ticker(sec_cache: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    companies = sec_cache.get("companies") if isinstance(sec_cache, dict) else {}
    if not isinstance(companies, dict):
        return {}
    output: Dict[str, Dict[str, float]] = {}
    for ticker, company in companies.items():
        if not isinstance(company, dict):
            continue
        markers = company.get("language_markers")
        if not isinstance(markers, dict) or not markers:
            continue
        output[str(ticker).upper()] = sec_language_scores(markers)
    return output


def build_signal(name: str, score: float, company_scores: Dict[str, float], direction: str, note: str, source: str = "alpha_vantage_cache") -> Dict[str, Any]:
    leaders = [ticker for ticker, _ in sorted(company_scores.items(), key=lambda item: item[1], reverse=True)[:3]]
    return {
        "name": name,
        "score": round(score, 1),
        "direction": direction,
        "leaders": leaders,
        "source": source,
        "note": note,
    }


def build_cache(alpha_cache: Dict[str, Any], sec_cache: Optional[Dict[str, Any]] = None, generated_at: Optional[str] = None) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_stamp()
    ticker_payloads = alpha_cache.get("tickers") if isinstance(alpha_cache, dict) else {}
    if not isinstance(ticker_payloads, dict):
        ticker_payloads = {}

    sec_scores = sec_language_by_ticker(sec_cache)
    metrics = [company_metrics(ticker, payload) for ticker, payload in sorted(ticker_payloads.items()) if isinstance(payload, dict)]
    for metric in metrics:
        language = sec_scores.get(metric["ticker"])
        if language:
            metric.update(language)
    matched_sec_count = sum(1 for metric in metrics if metric["ticker"] in sec_scores)

    productivity_scores: Dict[str, float] = {}
    bubble_scores: Dict[str, float] = {}
    for metric in metrics:
        ticker = metric["ticker"]
        base_productivity = productivity_component(metric)
        base_bubble = bubble_component(metric)
        if ticker in sec_scores:
            productivity_scores[ticker] = max(base_productivity, (base_productivity * 0.8) + (sec_scores[ticker]["sec_language_productivity_score"] * 0.2))
            bubble_scores[ticker] = max(base_bubble, (base_bubble * 0.8) + (sec_scores[ticker]["sec_language_bubble_risk_score"] * 0.2))
        else:
            productivity_scores[ticker] = base_productivity
            bubble_scores[ticker] = base_bubble

    productivity_score = average(productivity_scores.values()) or 50.0
    bubble_score = average(bubble_scores.values()) or 50.0
    source = "alpha_vantage_cache+sec_edgar_cache" if matched_sec_count else "alpha_vantage_cache"
    productivity_note = "Revenue growth, margins, free cash flow margin, R&D intensity, and optional SEC filing-language monetization evidence."
    bubble_note = "Capex growth, capex/revenue, debt growth, depreciation growth, free-cash-flow pressure, and optional SEC filing-language risk evidence."

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "source": source,
        "input_generated_at": alpha_cache.get("generated_at"),
        "company_metrics": [{key: rounded(value) if isinstance(value, float) else value for key, value in metric.items()} for metric in metrics],
        "signals": {
            "ai_productivity": build_signal(
                "AI Productivity Score",
                productivity_score,
                productivity_scores,
                "higher_is_better",
                productivity_note,
                source,
            ),
            "ai_capex_bubble_risk": build_signal(
                "AI Capex Bubble Risk",
                bubble_score,
                bubble_scores,
                "higher_is_riskier",
                bubble_note,
                source,
            ),
        },
        "metadata": {
            "company_count": len(metrics),
            "alpha_vantage_schema_version": alpha_cache.get("schema_version"),
            "sec_edgar_schema_version": sec_cache.get("schema_version") if isinstance(sec_cache, dict) and matched_sec_count else None,
            "sec_language_company_count": matched_sec_count,
        },
    }


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} root must be an object")
    return data


def write_cache(cache: Dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(cache, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Score AI productivity and capex-bubble signals from Alpha Vantage cache")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Input alpha-vantage-cache.json path")
    parser.add_argument("--sec-cache", type=Path, default=None, help="Optional sec-edgar-cache.json path for filing-language scoring")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output ai-signals-cache.json path")
    args = parser.parse_args()
    sec_cache = read_json(args.sec_cache) if args.sec_cache else None
    cache = build_cache(read_json(args.input), sec_cache=sec_cache)
    write_cache(cache, args.output)
    print(f"wrote {args.output} companies={cache['metadata']['company_count']} sec_language={cache['metadata']['sec_language_company_count']} productivity={cache['signals']['ai_productivity']['score']} bubble={cache['signals']['ai_capex_bubble_risk']['score']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
