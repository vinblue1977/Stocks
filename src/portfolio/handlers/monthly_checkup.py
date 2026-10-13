"""First-Saturday email: the household's mix vs. its own targets, risk vs. the market, and a Claude summary.

The summary is stored too, so the Checkup page can show it. Facts and the user's targets only; no advice.
"""

from __future__ import annotations

import textwrap
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from portfolio import checkup, llm, notify, report_pdf
from portfolio.buckets import AREAS
from portfolio.config import get_config
from portfolio.db import Repository
from portfolio.logs import get_logger, log

logger = get_logger(__name__)
SPLIT_EXAMPLE = 1000.0  # "next $1,000" in the email


def _pct(v: float | None, signed: bool = True) -> str:
    if v is None:
        return "n/a"
    return f"{v * 100:+.0f}%" if signed else f"{v * 100:.0f}%"


def summary_lines(summary: str) -> list[str]:
    """One wrapped bullet per point; the "unavailable" fallback stays a plain line."""
    points = [p.strip() for p in summary.splitlines() if p.strip()]
    if summary == llm.UNAVAILABLE or len(points) < 2:
        return [textwrap.fill(" ".join(points), width=72, initial_indent="  ", subsequent_indent="  ")]
    return [textwrap.fill(p, width=72, initial_indent="  • ", subsequent_indent="    ") for p in points]


def facts(built: dict[str, Any], targets: dict[str, Any] | None, alerts: int) -> dict[str, Any]:
    """What Claude sees: household numbers only (no names, no account details)."""
    home = built["views"]["household"]
    r = home["risk"]
    return {
        "as_of": built["as_of"],
        "value": round(home["total_value"], -2),
        "return_12_months": r["return_1y"],
        "market_return_12_months": (r.get("market") or {}).get("return_1y"),
        "swings_vs_market_beta": r["beta"],
        "volatility": r["volatility"],
        "market_volatility": (r.get("market") or {}).get("volatility"),
        "mix_vs_target": [
            {"area": d["label"], "share_now": round(d["now"], 3), "target": d["target"], "gap_dollars": round(d["gap"], -2)}
            for d in home["drift"] or []
        ],
        "single_sector_ceiling": (targets or {}).get("sector_ceiling"),
        "effective_number_of_holdings": home["effective_holdings"],
        "alerts_fired_this_month": alerts,
    }


def email(built: dict[str, Any], targets: dict[str, Any] | None, summary: str, now: datetime) -> str:
    home = built["views"]["household"]
    r = home["risk"]
    market = r.get("market") or {}
    lines = [
        f"Household: ${home['total_value']:,.0f}  ·  12 months: {_pct(r['return_1y'])} (market {_pct(market.get('return_1y'))})",
        "",
        "SUMMARY",
        *summary_lines(summary),
    ]
    if home["drift"]:
        lines += ["", "YOUR MIX VS. TARGET"]
        for d in sorted(home["drift"], key=lambda d: -abs(d["gap"])):
            if d["now"] or d["target"]:
                where = "over" if d["gap"] > 0 else "under"
                now_target = f"{_pct(d['now'], False):>4}  target {_pct(d['target'], False):>4}"
                lines.append(f"  {d['label']:<24} {now_target}   {where} by ${abs(d['gap']):,.0f}")
        targets_pct = {k: v["pct"] / 100 for k, v in targets["areas"].items()}
        parts = checkup.split(home["by_area"], home["total_value"], targets_pct, SPLIT_EXAMPLE)
        if parts:
            lines += ["", f"NEXT ${SPLIT_EXAMPLE:,.0f}, BY YOUR TARGETS"]
            for area, amount in parts:
                fund = targets["areas"].get(area, {}).get("fund") or ""
                lines.append(f"  {AREAS[area]:<24} ${amount:,.0f}  {f'({fund})' if fund else ''}".rstrip())
    else:
        lines += ["", "Set your target mix on the Checkup page to see how far the portfolio is from it."]
    lines += ["", "RISK"]
    if r["beta"] is not None:
        vols = f"{_pct(r['volatility'], False)} vs {_pct(market.get('volatility'), False)}"
        lines.append(f"  Swings {r['beta']:.1f}x the market (volatility {vols})")
    if r.get("worst_drop_2022") is not None:
        lines.append(f"  Worst drop in 2022: {_pct(r['worst_drop_2022'])} (market {_pct(market.get('worst_drop_2022'))})")
    site = get_config().site_url
    if site:
        lines += ["", f"Full checkup: {site}/#/checkup"]
    lines += ["", f"Prices as of {built['as_of']} close. Describes your data and the targets you set."]
    return "\n".join(lines)


def short_email(built: dict[str, Any], targets: dict[str, Any] | None) -> str:
    """The few lines that go with the PDF."""
    home = built["views"]["household"]
    r = home["risk"]
    market = _pct((r.get("market") or {}).get("return_1y"))
    lines = [f"Household: ${home['total_value']:,.0f}  ·  12 months: {_pct(r['return_1y'])} (market {market})"]
    gaps = sorted((d for d in home["drift"] or [] if d["area"] in AREAS), key=lambda d: -abs(d["gap"]))
    if gaps:
        d = gaps[0]
        lines.append(f"Biggest gap: {d['label']} at {_pct(d['now'], False)} vs. your {_pct(d['target'], False)} target")
    lines += ["", "Your full checkup is attached as a PDF."]
    site = get_config().site_url
    if site:
        lines.append(f"Live version: {site}/#/checkup")
    return "\n".join(lines)


def deliver(built: dict[str, Any], targets: dict[str, Any] | None, summary: str, now: datetime) -> None:
    """PDF by SES; if that fails (PDF, SES, unverified address), the plain-text email by SNS so the month isn't missed."""
    subject = f"Your monthly portfolio checkup, {now:%b %d}"
    try:
        cfg = get_config()
        pdf = report_pdf.build_pdf(built, targets, summary, {"mine": cfg.owner_name, "partner": cfg.partner_name}, f"{now:%B %Y}")
        notify.send_email_with_pdf(subject, short_email(built, targets), f"Checkup-{now:%Y-%m}.pdf", pdf)
    except Exception:
        logger.exception("PDF checkup email failed; sending plain text")
        notify.send_email(subject, email(built, targets, summary, now))


def handler(event: dict[str, Any] | None, context: Any, repo: Repository | None = None, summarizer: Any = llm) -> dict[str, Any]:
    repo = repo or Repository()
    now = datetime.now(UTC)
    by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for holding in repo.all_holdings():
        by_user[holding["user_id"]].append(holding)
    month = now.strftime("%Y-%m")
    force = bool((event or {}).get("force"))  # a manual run can send again in the same month
    sent, skipped, failed = 0, 0, []
    for user_id, holdings in by_user.items():
        # A retry after a partial failure skips users already emailed this month.
        last = repo.latest_checkup_summary(user_id)
        if last and last.get("month") == month and not force:
            skipped += 1
            continue
        try:
            targets = repo.get_targets(user_id)
            built = checkup.build(repo, holdings, targets)
            if not built["views"]["household"]["total_value"]:
                continue
            cutoff = (now - timedelta(days=31)).isoformat(timespec="seconds")
            alerts = sum(1 for a in repo.list_alerts(user_id, limit=500) if a["created_at"] >= cutoff)
            summary = summarizer.checkup_summary(facts(built, targets, alerts))
            deliver(built, targets, summary, now)
            record = {
                "summary": summary,
                "month": month,
                "created_at": now.isoformat(timespec="seconds"),
                "as_of": built["as_of"],
            }
            repo.put_checkup_summary(user_id, month, record)
            sent += 1
        except Exception:
            logger.exception("monthly checkup failed")
            failed.append(user_id)
    log(logger, "monthly_checkup_complete", emails=sent, skipped=skipped, failed=len(failed))
    if failed:
        raise RuntimeError(f"Monthly checkup failed for {len(failed)} user(s)")  # retried; done users are skipped
    return {"emails_sent": sent, "skipped": skipped}
