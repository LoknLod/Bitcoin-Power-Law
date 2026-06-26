#!/usr/bin/env python3
"""BTC/AIM cockpit instrument check and weekly product review packet.

Default behavior is watchdog-friendly: print nothing and exit 0 when the
instrument is healthy; print a concise failure report and exit non-zero when a
critical check fails. The optional review mode writes local JSON/Markdown packets
for human approval-gated product improvement. It never sends messages, executes
trades, or mutates broker/wallet/account state.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRIVATE_SERVE_DIR = Path("/Users/dougtee/.hermes/work/private-sites/btc-power-law-private")
DEFAULT_HOST_SCRIPTS = [
    Path("/Users/dougtee/.hermes/scripts/serve_btc_power_law_private.sh"),
    Path("/Users/dougtee/.hermes/scripts/shrike_portfolio_cockpit_refresh.sh"),
    Path("/Users/dougtee/.hermes/scripts/shrike_btc_widget_price_refresh.sh"),
]
DEFAULT_REVIEW_DIR = Path("/Volumes/ShrikeAI/Shrike/System/Hermes/state/btc-aim-product-review")
PUBLIC_CACHE_FILES = ["aim-cache.json", "market-cache.json", "market-history-cache.json"]
ACTIVE_PAGES = ["index.html", "power-law.html", "macro.html"]
REDIRECT_SHIMS = {
    "quick.html": "index.html",
    "aim.html": "index.html",
    "gold.html": "macro.html",
    "energy-metrics.html": "macro.html",
}
SENSITIVE_MARKERS = [
    "privacy_note",
    "target_portfolio",
    "schwab_total",
    "account_count",
    "pension_annual",
    "mutation_allowed",
    "retirement_plan",
    "PCRA Trust -",
]
MARKET_STALE_MINUTES = 90
AIM_STALE_HOURS = 72


@dataclass
class Finding:
    severity: str
    check: str
    message: str
    detail: str | None = None


@dataclass
class Report:
    schema_version: str = "btc_aim_product_check.v0.1"
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat())
    root: str = str(ROOT)
    status: str = "ok"
    findings: list[Finding] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    proposed_changes: list[str] = field(default_factory=list)

    def add(self, severity: str, check: str, message: str, detail: str | None = None) -> None:
        self.findings.append(Finding(severity, check, message, detail))
        if severity == "critical":
            self.status = "critical"
        elif severity == "warning" and self.status == "ok":
            self.status = "warning"


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} root must be a JSON object")
    return data


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def age_minutes(value: Any, now: datetime) -> float | None:
    dt = parse_time(value)
    if not dt:
        return None
    return (now - dt).total_seconds() / 60


def age_label(minutes: float | None) -> str:
    if minutes is None:
        return "unknown"
    if minutes < 1:
        return "now"
    if minutes < 60:
        return f"{round(minutes)}m"
    hours = minutes / 60
    if hours < 48:
        return f"{hours:.1f}h" if hours < 10 else f"{round(hours)}h"
    return f"{round(hours / 24)}d"


def run(cmd: list[str], cwd: Path = ROOT, timeout: int = 30) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - diagnostic tool
        return 255, str(exc)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def check_required_files(report: Report) -> None:
    required = ACTIVE_PAGES + list(REDIRECT_SHIMS) + PUBLIC_CACHE_FILES + ["widget.js"]
    for name in required:
        if not (ROOT / name).exists():
            report.add("critical", "required_files", f"Missing required site file: {name}")


def check_json_caches(report: Report, now: datetime, private_serve_dir: Path | None = None) -> None:
    for name in PUBLIC_CACHE_FILES:
        path = ROOT / name
        try:
            data = read_json(path)
        except Exception as exc:  # noqa: BLE001
            report.add("critical", "json_cache", f"Public cache does not parse: {name}", str(exc))
            continue
        report.metrics.setdefault("public_caches", {})[name] = {
            "generated_at": data.get("generated_at"),
            "source": data.get("source"),
            "freshness": data.get("freshness"),
        }
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits = [marker for marker in SENSITIVE_MARKERS if marker.lower() in text.lower()]
        if hits:
            report.add("critical", "public_privacy", f"Public cache contains private-shaped markers: {name}", ", ".join(hits))

    market_candidates = [("public", ROOT / "market-cache.json")]
    if private_serve_dir is not None:
        market_candidates.insert(0, ("private", private_serve_dir / "market-cache.json"))
    for market_label, market in market_candidates:
        if not market.exists():
            continue
        try:
            data = read_json(market)
            btc = ((data.get("assets") or {}).get("btc_usd") or {})
            observed = btc.get("as_of") or data.get("generated_at")
            minutes = age_minutes(observed, now)
            report.metrics["market_freshness"] = {"source": market_label, "observed_at": observed, "age": age_label(minutes), "threshold": f"{MARKET_STALE_MINUTES}m"}
            if minutes is None or minutes > MARKET_STALE_MINUTES:
                report.add("warning", "market_freshness", "BTC market cache is stale or age-unknown", f"source={market_label} age={age_label(minutes)} threshold={MARKET_STALE_MINUTES}m")
            break
        except Exception as exc:  # noqa: BLE001
            report.add("critical", "market_freshness", "Cannot inspect market cache freshness", str(exc))
            break

    aim = (ROOT / "aim-cache.json")
    if aim.exists():
        try:
            data = read_json(aim)
            minutes = age_minutes(data.get("generated_at"), now)
            stale_signals = []
            for signal in data.get("signals", []):
                if not isinstance(signal, dict):
                    continue
                freshness = str(signal.get("freshness") or "")
                age_days = signal.get("age_days")
                age_is_stale = isinstance(age_days, (int, float)) and age_days > 90
                if freshness in {"stale", "future", "unknown"} or age_is_stale:
                    stale_signals.append(signal.get("name") or signal.get("key") or "unnamed")
            report.metrics["regime_freshness"] = {
                "generated_at": data.get("generated_at"),
                "age": age_label(minutes),
                "threshold": f"{AIM_STALE_HOURS}h",
                "freshness": data.get("freshness"),
                "stale_signal_count": len(stale_signals),
                "stale_signal_examples": stale_signals[:5],
            }
            if minutes is None or minutes > AIM_STALE_HOURS * 60 or data.get("freshness") == "stale":
                report.add("warning", "regime_freshness", "AIM/regime cache is stale or marked stale", "; ".join(stale_signals[:5]) or None)
        except Exception as exc:  # noqa: BLE001
            report.add("critical", "regime_freshness", "Cannot inspect AIM cache freshness", str(exc))


def check_navigation(report: Report) -> None:
    expected = [('Dashboard', 'index.html'), ('Power Law', 'power-law.html'), ('Macro', 'macro.html')]
    nav_re = re.compile(r'<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
    for page in ACTIVE_PAGES:
        html = (ROOT / page).read_text(encoding="utf-8", errors="ignore") if (ROOT / page).exists() else ""
        nav_match = re.search(r'<div class="nav-links">(.*?)</div>', html, re.S)
        items = [] if not nav_match else [(re.sub(r"<.*?>", "", label).strip(), href) for href, label in nav_re.findall(nav_match.group(1))]
        if items != expected:
            report.add("critical", "nav_sprawl", f"{page} top nav drifted from Dashboard/Power Law/Macro", repr(items))
    for page, target in REDIRECT_SHIMS.items():
        path = ROOT / page
        if path.exists() and "nav-links" in path.read_text(encoding="utf-8", errors="ignore"):
            report.add("warning", "nav_sprawl", f"Redirect shim {page} appears to contain navigation", f"expected redirect to {target}")


def check_private_overlay(report: Report, serve_dir: Path, endpoint: str | None) -> None:
    private_cache = serve_dir / "portfolio-cockpit-cache.json"
    if not serve_dir.exists():
        report.add("warning", "private_overlay", "Private serve directory is missing", str(serve_dir))
        return
    if not private_cache.exists():
        report.add("warning", "private_overlay", "Private portfolio cockpit cache is missing", str(private_cache))
        return
    try:
        data = read_json(private_cache)
    except Exception as exc:  # noqa: BLE001
        report.add("critical", "private_overlay", "Private cache does not parse", str(exc))
        return
    if data.get("schema_version") != "shrike_portfolio_cockpit.v0.1" or data.get("mutation_allowed") is not False:
        report.add("critical", "private_overlay", "Private cache failed schema/read-only validation")
    report.metrics["private_overlay"] = {
        "generated_at": data.get("generated_at"),
        "freshness": data.get("freshness"),
        "portfolio_source_at": (data.get("portfolio") or {}).get("source_generated_at"),
        "aim_posture": ((data.get("aim") or {}).get("posture") or {}).get("label"),
        "hard_money_score_available": (data.get("aim") or {}).get("hard_money_score") is not None,
    }

    if endpoint:
        url = endpoint.rstrip("/") + "/portfolio-cockpit-cache.json"
        try:
            with urlopen(url, timeout=3) as response:  # noqa: S310 - local Tailscale/private diagnostic
                if response.status != 200:
                    report.add("warning", "private_endpoint", "Private endpoint returned non-200", str(response.status))
                    return
                remote = json.loads(response.read().decode("utf-8"))
                if not isinstance(remote, dict):
                    raise ValueError("remote private cache root is not an object")
                if remote.get("schema_version") != "shrike_portfolio_cockpit.v0.1" or remote.get("mutation_allowed") is not False or not remote.get("privacy_note"):
                    report.add("critical", "private_endpoint", "Private endpoint body failed schema/read-only/privacy validation")
                elif remote.get("generated_at") != data.get("generated_at"):
                    report.add("warning", "private_endpoint", "Private endpoint generated_at differs from local served cache", f"remote={remote.get('generated_at')} local={data.get('generated_at')}")
        except (OSError, URLError) as exc:
            report.add("warning", "private_endpoint", "Private endpoint not reachable", str(exc))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            report.add("critical", "private_endpoint", "Private endpoint returned invalid cache body", str(exc))


def check_host_scripts(report: Report, host_scripts: Iterable[Path]) -> None:
    existing = [str(path) for path in host_scripts if path.exists()]
    missing = [str(path) for path in host_scripts if not path.exists()]
    for path in missing:
        report.add("warning", "host_scripts", "Expected host script missing", path)
    if existing:
        code, out = run(["bash", "-n", *existing], cwd=ROOT)
        if code != 0:
            report.add("critical", "host_scripts", "Host script bash -n failed", out)


def check_repo_state(report: Report) -> None:
    code, out = run(["git", "status", "--short", "--branch"], cwd=ROOT)
    if code != 0:
        report.add("warning", "repo_state", "Could not inspect git status", out)
        return
    report.metrics["git_status"] = out
    dirty_lines = [line for line in out.splitlines()[1:] if line.strip()]
    if dirty_lines:
        report.add("warning", "repo_state", "Repo has local changes; product cron pulls are safer from a clean checkout", " | ".join(dirty_lines[:6]))


def build_report(args: argparse.Namespace) -> Report:
    now = datetime.now(timezone.utc)
    report = Report()
    check_required_files(report)
    check_json_caches(report, now, Path(args.private_serve_dir))
    check_navigation(report)
    check_private_overlay(report, Path(args.private_serve_dir), args.private_endpoint)
    check_host_scripts(report, [Path(p) for p in args.host_script])
    check_repo_state(report)
    report.proposed_changes = proposed_changes(report)
    return report


def proposed_changes(report: Report) -> list[str]:
    changes: list[str] = []
    checks = {finding.check for finding in report.findings}
    if "market_freshness" in checks:
        changes.append("Inspect BTC/gold market refresh cron before trusting widget spot/fair-value read.")
    if "regime_freshness" in checks:
        changes.append("Refresh or repair stale macro/FRED inputs; keep AIM posture directional until stale inputs clear.")
    if "repo_state" in checks:
        changes.append("Clean/rebase the repo after generated cache updates so private cockpit cron can fast-forward cleanly.")
    if "nav_sprawl" in checks:
        changes.append("Restore the three-tab cockpit boundary: Dashboard, Power Law, Macro.")
    if not changes:
        changes.append("No product mutation proposed. Keep watching freshness, privacy, and repo cleanliness.")
    return changes


def as_jsonable(report: Report) -> dict[str, Any]:
    data = asdict(report)
    data["findings"] = [asdict(f) for f in report.findings]
    return data


def markdown(report: Report) -> str:
    severity_icon = {"critical": "❌", "warning": "⚠️", "info": "ℹ️"}
    lines = [
        "# BTC/AIM Product Review",
        "",
        f"Generated: `{report.generated_at}`",
        f"Status: **{report.status.upper()}**",
        "",
        "## Freshness",
    ]
    market = report.metrics.get("market_freshness", {})
    regime = report.metrics.get("regime_freshness", {})
    lines += [
        f"- Market cache: `{market.get('age', 'unknown')}` old, threshold `{market.get('threshold', '90m')}`.",
        f"- AIM/regime cache: `{regime.get('age', 'unknown')}` old, freshness `{regime.get('freshness', 'unknown')}`, stale signals `{regime.get('stale_signal_count', 0)}`.",
    ]
    examples = regime.get("stale_signal_examples") or []
    if examples:
        lines.append(f"- Stale examples: {', '.join(map(str, examples[:5]))}.")
    lines += ["", "## Findings"]
    if report.findings:
        for finding in report.findings:
            detail = f" — {finding.detail}" if finding.detail else ""
            lines.append(f"- {severity_icon.get(finding.severity, '•')} **{finding.check}**: {finding.message}{detail}")
    else:
        lines.append("- No findings.")
    lines += ["", "## Proposed changes", ""]
    for change in report.proposed_changes:
        lines.append(f"- {change}")
    lines += ["", "Approval wall: this packet proposes review work only. It performs no trades, account changes, wallet actions, or Telegram self-sends.", ""]
    return "\n".join(lines)


def write_review(report: Report, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "latest.json"
    md_path = out_dir / "latest.md"
    json_path.write_text(json.dumps(as_jsonable(report), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(markdown(report), encoding="utf-8")


def print_failure_report(report: Report, *, include_warnings: bool = False) -> None:
    if report.status == "ok" or (report.status == "warning" and not include_warnings):
        return
    print(f"BTC/AIM product check: {report.status.upper()}")
    for finding in report.findings:
        if finding.severity in {"critical", "warning"}:
            detail = f" ({finding.detail})" if finding.detail else ""
            print(f"- {finding.severity}: {finding.check}: {finding.message}{detail}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-serve-dir", default=os.environ.get("BTC_AIM_PRIVATE_SERVE_DIR", str(DEFAULT_PRIVATE_SERVE_DIR)))
    parser.add_argument("--private-endpoint", default=os.environ.get("BTC_AIM_PRIVATE_ENDPOINT", ""), help="Optional private base URL to probe, e.g. Tailscale cockpit URL")
    parser.add_argument("--host-script", action="append", default=[str(p) for p in DEFAULT_HOST_SCRIPTS])
    parser.add_argument("--review-dir", default=os.environ.get("BTC_AIM_REVIEW_DIR", str(DEFAULT_REVIEW_DIR)))
    parser.add_argument("--write-review", action="store_true", help="Write latest.json/latest.md product-review packet")
    parser.add_argument("--json", action="store_true", help="Print full JSON report")
    parser.add_argument("--markdown", action="store_true", help="Print Markdown report")
    parser.add_argument("--verbose-ok", action="store_true", help="Print a one-line OK message instead of staying quiet")
    parser.add_argument("--show-warnings", action="store_true", help="Print warnings in watchdog mode; default only prints critical failures")
    parser.add_argument("--warnings-fail", action="store_true", help="Exit non-zero on warnings as well as critical findings")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_report(args)
    if args.write_review:
        write_review(report, Path(args.review_dir))
    if args.json:
        print(json.dumps(as_jsonable(report), indent=2, sort_keys=True))
    elif args.markdown:
        print(markdown(report))
    elif report.status == "ok":
        if args.verbose_ok:
            print("BTC/AIM product check: OK")
    else:
        print_failure_report(report, include_warnings=args.show_warnings or args.warnings_fail)
    if report.status == "critical" or (args.warnings_fail and report.status == "warning"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
