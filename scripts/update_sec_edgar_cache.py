#!/usr/bin/env python3
"""Refresh the local SEC EDGAR cache for AIM filing-language signals."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
SEC_EDGAR_CACHE = ROOT / "sec-edgar-cache.json"
SCHEMA_VERSION = "sec_edgar_cache.v0.1"
DEFAULT_USER_AGENT = ""

DEFAULT_TICKERS = ["MSFT", "GOOGL", "AMZN", "META", "NVDA", "AVGO", "AMD", "ORCL", "TSM"]

# Static CIK map keeps the collector deterministic and avoids another network dependency.
TICKER_CIKS = {
    "MSFT": "0000789019",
    "GOOGL": "0001652044",
    "AMZN": "0001018724",
    "META": "0001326801",
    "NVDA": "0001045810",
    "AVGO": "0001730168",
    "AMD": "0000002488",
    "ORCL": "0001341439",
    "TSM": "0001046179",
}

RELEVANT_FACT_TAGS = {
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "CostOfRevenue",
    "GrossProfit",
    "OperatingIncomeLoss",
    "NetIncomeLoss",
    "ResearchAndDevelopmentExpense",
    "CapitalExpendituresIncurredButNotYetPaid",
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PropertyPlantAndEquipmentNet",
    "DepreciationDepletionAndAmortization",
    "LongTermDebtCurrent",
    "LongTermDebtNoncurrent",
    "ShortTermBorrowings",
    "CashAndCashEquivalentsAtCarryingValue",
}

LANGUAGE_PATTERNS = {
    "ai_mentions": re.compile(r"\b(ai|artificial intelligence|generative ai|machine learning|large language model|llm)\b", re.I),
    "capex_infrastructure_mentions": re.compile(
        r"\b(capital expenditure|capex|data centers?|datacenters?|infrastructure|gpus?|accelerators?|compute capacity|servers?)\b",
        re.I,
    ),
    "monetization_mentions": re.compile(r"\b(monetization|monetize|revenue|cloud revenue|customer adoption|demand)\b", re.I),
    "energy_constraint_mentions": re.compile(
        r"\b(power|electricity|energy|grid|capacity constraint|supply constraint|availability)\b",
        re.I,
    ),
    "obligation_risk_mentions": re.compile(
        r"\b(purchase obligation|lease obligation|commitment|risk factor|uncertain|impairment|useful life)\b",
        re.I,
    ),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def cik_int(cik: str) -> str:
    return str(int(cik))


def archive_accession(accession: str) -> str:
    return accession.replace("-", "")


def filing_url(cik: str, accession: str, primary_document: str) -> str:
    return (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{cik_int(cik)}/{archive_accession(accession)}/{primary_document}"
    )


def fetch_json(url: str, user_agent: str, timeout: int = 30) -> Dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_text(url: str, user_agent: str, timeout: int = 30) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
    return raw


def latest_filings(cik: str, submissions: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    recent = submissions.get("filings", {}).get("recent", {}) if isinstance(submissions, dict) else {}
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    primary_docs = recent.get("primaryDocument", [])
    latest: Dict[str, Dict[str, str]] = {}

    for form, accession, filing_date, primary_doc in zip(forms, accessions, dates, primary_docs):
        if form not in {"10-K", "10-Q"} or form in latest:
            continue
        latest[form] = {
            "form": str(form),
            "accession_number": str(accession),
            "filing_date": str(filing_date),
            "primary_document": str(primary_doc),
            "url": filing_url(cik, str(accession), str(primary_doc)),
        }
        if "10-K" in latest and "10-Q" in latest:
            break
    return latest


def company_fact_subset(facts: Dict[str, Any]) -> Dict[str, Any]:
    us_gaap = facts.get("facts", {}).get("us-gaap", {}) if isinstance(facts, dict) else {}
    if not isinstance(us_gaap, dict):
        return {}
    return {tag: value for tag, value in us_gaap.items() if tag in RELEVANT_FACT_TAGS}


def normalize_text(text: str) -> str:
    text = re.sub(r"<script\b.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def extract_language_markers(text: str, max_snippets: int = 5) -> Dict[str, Any]:
    normalized = normalize_text(text)
    markers: Dict[str, Any] = {}
    snippets: List[str] = []
    for name, pattern in LANGUAGE_PATTERNS.items():
        matches = list(pattern.finditer(normalized))
        markers[name] = len(matches)
        for match in matches[:2]:
            if len(snippets) >= max_snippets:
                break
            start = max(0, match.start() - 90)
            end = min(len(normalized), match.end() + 160)
            snippet = normalized[start:end].strip()
            if snippet not in snippets:
                snippets.append(snippet)
    markers["snippets"] = snippets
    return markers


def validate_user_agent(user_agent: str) -> str:
    user_agent = (user_agent or "").strip()
    if not user_agent or "@" not in user_agent:
        raise ValueError("SEC EDGAR requires an identifiable User-Agent with contact email; set SEC_EDGAR_USER_AGENT or pass --user-agent.")
    return user_agent


def redact_error(error: Exception, user_agent: str = "") -> str:
    message = str(error)
    if user_agent:
        message = message.replace(user_agent, "[USER_AGENT_REDACTED]")
    # Keep errors useful, but avoid preserving long tokens or accidental secrets.
    message = re.sub(r"[A-Za-z0-9_\-]{24,}", "[REDACTED]", message)
    return message[:500]


def build_cache(
    tickers: Iterable[str],
    user_agent: str,
    fetch_json: Callable[[str, str], Dict[str, Any]] = fetch_json,
    fetch_text: Callable[[str, str], str] = fetch_text,
    include_filing_text: bool = False,
    sleep_seconds: float = 0.1,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now()
    companies: Dict[str, Any] = {}
    errors: Dict[str, List[str]] = {}

    for raw_ticker in tickers:
        ticker = raw_ticker.strip().upper()
        if not ticker:
            continue
        cik = TICKER_CIKS.get(ticker)
        if cik is None:
            errors[ticker] = ["Unknown ticker; add a CIK mapping before fetching SEC EDGAR data."]
            continue

        company_errors: List[str] = []
        submissions: Dict[str, Any] = {}
        facts: Dict[str, Any] = {}
        try:
            submissions = fetch_json(f"https://data.sec.gov/submissions/CIK{cik}.json", user_agent)
        except Exception as exc:  # noqa: BLE001 - cache should record provider failures and continue.
            company_errors.append(f"submissions: {redact_error(exc, user_agent)}")
        time.sleep(max(0.0, sleep_seconds))

        try:
            facts = fetch_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", user_agent)
        except Exception as exc:  # noqa: BLE001
            company_errors.append(f"companyfacts: {redact_error(exc, user_agent)}")
        time.sleep(max(0.0, sleep_seconds))

        filings = latest_filings(cik, submissions)
        language_markers: Dict[str, Any] = {}
        if include_filing_text:
            combined_text = []
            for filing in filings.values():
                try:
                    combined_text.append(fetch_text(filing["url"], user_agent))
                except Exception as exc:  # noqa: BLE001
                    company_errors.append(f"filing_text {filing.get('form', 'unknown')}: {redact_error(exc, user_agent)}")
                time.sleep(max(0.0, sleep_seconds))
            if combined_text:
                language_markers = extract_language_markers("\n".join(combined_text))

        company = {
            "ticker": ticker,
            "cik": cik,
            "name": submissions.get("name") or facts.get("entityName") or ticker,
            "latest_filings": filings,
            "company_facts": company_fact_subset(facts),
            "language_markers": language_markers,
            "errors": company_errors,
        }
        companies[ticker] = company
        if company_errors:
            errors[ticker] = company_errors

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "source": "sec_edgar_api",
        "metadata": {
            "company_count": len(companies),
            "tickers": sorted(companies),
            "included_filing_text": include_filing_text,
            "fact_tags": sorted(RELEVANT_FACT_TAGS),
        },
        "companies": companies,
        "errors": errors,
    }


def parse_tickers(value: str) -> List[str]:
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a local SEC EDGAR filing/facts cache for AIM AI signals.")
    parser.add_argument("--tickers", default=",".join(DEFAULT_TICKERS), help="Comma-separated tickers to fetch.")
    parser.add_argument("--output", type=Path, default=SEC_EDGAR_CACHE, help="Output JSON path.")
    parser.add_argument(
        "--user-agent",
        default=os.environ.get("SEC_EDGAR_USER_AGENT", DEFAULT_USER_AGENT),
        help="Identifiable SEC User-Agent with contact email. Defaults to SEC_EDGAR_USER_AGENT.",
    )
    parser.add_argument("--include-filing-text", action="store_true", help="Fetch latest 10-K/10-Q HTML and extract language markers.")
    parser.add_argument("--sleep-seconds", type=float, default=0.1, help="Delay between SEC requests.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        user_agent = validate_user_agent(args.user_agent)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2
    cache = build_cache(
        tickers=parse_tickers(args.tickers),
        user_agent=user_agent,
        include_filing_text=args.include_filing_text,
        sleep_seconds=args.sleep_seconds,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"Wrote {args.output} | companies={cache['metadata']['company_count']} | "
        f"errors={sum(len(v) for v in cache['errors'].values())} | filing_text={cache['metadata']['included_filing_text']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
