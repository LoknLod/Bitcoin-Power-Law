#!/usr/bin/env python3
"""Run the BTC/AIM cache refresh pipeline and write a machine-readable report."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "aim-pipeline-report.json"
AI_TICKERS = "MSFT,GOOGL,AMZN,META,NVDA,AVGO,AMD,ORCL,TSM"


@dataclass(frozen=True)
class Step:
    label: str
    command: List[str]
    timeout: int = 300


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[List[str], Path, int], CommandResult]


def utc_now_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_as_of(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--as-of must use YYYY-MM-DD") from exc


def default_as_of() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def python_script(script_name: str) -> str:
    return str(Path("scripts") / script_name)


def build_plan(
    as_of: str,
    include_ai: bool,
    include_sec: bool,
    include_filing_text: bool,
    sec_user_agent: Optional[str],
    offline_market: bool,
    tickers: str = AI_TICKERS,
) -> List[Step]:
    plan = [
        Step("update_fred_cache", [sys.executable, python_script("update_fred_cache.py"), "--as-of", as_of]),
        Step(
            "update_market_cache",
            [
                sys.executable,
                python_script("update_market_cache.py"),
                *( ["--offline"] if offline_market else [] ),
                "--as-of",
                as_of,
            ],
        ),
    ]

    if include_ai:
        plan.append(Step("update_alpha_vantage_cache", [sys.executable, python_script("update_alpha_vantage_cache.py"), "--tickers", tickers], timeout=1800))
        if include_sec:
            sec_cmd = [
                sys.executable,
                python_script("update_sec_edgar_cache.py"),
                "--tickers",
                tickers,
            ]
            if sec_user_agent:
                sec_cmd.extend(["--user-agent", sec_user_agent])
            if include_filing_text:
                sec_cmd.append("--include-filing-text")
            plan.append(Step("update_sec_edgar_cache", sec_cmd, timeout=1800))

        score_cmd = [sys.executable, python_script("score_ai_signals.py")]
        if include_sec:
            score_cmd.extend(["--sec-cache", "sec-edgar-cache.json"])
        plan.append(Step("score_ai_signals", score_cmd))

    aim_cmd = [sys.executable, python_script("score_aim_macro.py"), "--as-of", as_of]
    if include_ai:
        aim_cmd.extend(["--ai-signals-cache", "ai-signals-cache.json"])
    plan.append(Step("score_aim_macro", aim_cmd))
    return plan


def subprocess_runner(command: List[str], cwd: Path, timeout: int) -> CommandResult:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.output or ""
        stderr = exc.stderr or f"Command timed out after {timeout} seconds"
        return CommandResult(124, str(stdout), str(stderr))
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def redaction_values(env: Mapping[str, str], sec_user_agent: Optional[str]) -> List[str]:
    names = [
        "ALPHA_VANTAGE_STOCK_API",
        "ALPHAVANTAGE_API_KEY",
        "SEC_EDGAR_USER_AGENT",
        "BWS_ACCESS_TOKEN",
        "GITHUB_TOKEN",
    ]
    values = [env.get(name, "") for name in names]
    if sec_user_agent:
        values.append(sec_user_agent)
    return [value for value in values if value]


def redact(text: str, values: Iterable[str]) -> str:
    redacted = text
    for value in values:
        if len(value) < 4:
            continue
        redacted = redacted.replace(value, "[REDACTED]")
    redacted = re.sub(r"(?i)(api[_-]?key=)[A-Za-z0-9._:-]+", r"\1[REDACTED]", redacted)
    return redacted


def step_to_report(step: Step, status: str, result: Optional[CommandResult], redactions: List[str]) -> Dict[str, object]:
    item: Dict[str, object] = {
        "label": step.label,
        "command": [redact(part, redactions) for part in step.command],
        "status": status,
    }
    if result is not None:
        item.update(
            {
                "returncode": result.returncode,
                "stdout": redact(result.stdout.strip(), redactions),
                "stderr": redact(result.stderr.strip(), redactions),
            }
        )
    return item


def write_report(path: Path, report: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_pipeline(
    as_of: str,
    include_ai: bool,
    include_sec: bool,
    include_filing_text: bool,
    sec_user_agent: Optional[str],
    offline_market: bool,
    dry_run: bool,
    report_path: Path,
    runner: Runner = subprocess_runner,
    env: Optional[Mapping[str, str]] = None,
    tickers: str = AI_TICKERS,
) -> int:
    environment = env if env is not None else os.environ
    include_sec = include_sec or include_filing_text
    include_ai = include_ai or include_sec
    plan = build_plan(as_of, include_ai, include_sec, include_filing_text, sec_user_agent, offline_market, tickers=tickers)
    redactions = redaction_values(environment, sec_user_agent)
    report_steps: List[Dict[str, object]] = []
    exit_code = 0
    failed = False

    for step in plan:
        if failed:
            report_steps.append(step_to_report(step, "skipped", None, redactions))
            continue
        if dry_run:
            report_steps.append(step_to_report(step, "planned", None, redactions))
            continue
        try:
            result = runner(step.command, ROOT, step.timeout)
        except subprocess.TimeoutExpired as exc:
            result = CommandResult(
                124,
                str(exc.output or ""),
                str(exc.stderr or f"Command timed out after {step.timeout} seconds"),
            )
        except Exception as exc:  # defensive: reports must survive runner failures
            result = CommandResult(1, "", f"Runner error: {exc.__class__.__name__}: {exc}")
        if result.returncode == 0:
            report_steps.append(step_to_report(step, "success", result, redactions))
        else:
            failed = True
            exit_code = result.returncode or 1
            report_steps.append(step_to_report(step, "failed", result, redactions))

    status = "planned" if dry_run else "failed" if failed else "success"
    report = {
        "schema_version": "aim_pipeline_report.v0.1",
        "generated_at": utc_now_stamp(),
        "as_of": as_of,
        "status": status,
        "options": {
            "include_ai": include_ai,
            "include_sec": include_sec,
            "include_filing_text": include_filing_text,
            "offline_market": offline_market,
            "dry_run": dry_run,
            "tickers": tickers,
        },
        "steps": report_steps,
    }
    write_report(report_path, report)
    return exit_code


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the BTC/AIM cache refresh pipeline.")
    parser.add_argument("--as-of", type=parse_as_of, default=default_as_of(), help="As-of date in YYYY-MM-DD. Defaults to today's UTC date.")
    parser.add_argument("--include-ai", action="store_true", help="Refresh Alpha Vantage AI fundamentals and feed ai-signals-cache.json into AIM.")
    parser.add_argument("--include-sec", action="store_true", help="Refresh SEC EDGAR cache and blend it into AI scoring. Implies --include-ai.")
    parser.add_argument("--include-filing-text", action="store_true", help="Download latest filing text for SEC language markers.")
    parser.add_argument("--sec-user-agent", default=os.environ.get("SEC_EDGAR_USER_AGENT"), help="Contactable SEC user-agent. Defaults to SEC_EDGAR_USER_AGENT.")
    parser.add_argument("--online-market", action="store_true", help="Use live market APIs. Without this, market cache is rendered in offline/replay mode.")
    parser.add_argument("--dry-run", action="store_true", help="Write planned commands without running them.")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="Pipeline report path. Defaults to aim-pipeline-report.json.")
    parser.add_argument("--tickers", default=AI_TICKERS, help="Comma-separated AI ticker universe.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    include_sec = args.include_sec or args.include_filing_text
    include_ai = args.include_ai or include_sec
    if include_sec and not args.sec_user_agent:
        print("SEC pipeline requires --sec-user-agent or SEC_EDGAR_USER_AGENT", file=sys.stderr)
        return 2
    report_path = args.report if args.report.is_absolute() else ROOT / args.report
    status = run_pipeline(
        as_of=args.as_of,
        include_ai=include_ai,
        include_sec=include_sec,
        include_filing_text=args.include_filing_text,
        sec_user_agent=args.sec_user_agent,
        offline_market=not args.online_market,
        dry_run=args.dry_run,
        report_path=report_path,
        tickers=args.tickers,
    )
    print(f"Wrote {report_path}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
