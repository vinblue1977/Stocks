"""Sunday email: the week's change in value, best and worst holdings, and alerts that fired."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from portfolio import analytics, notify
from portfolio.config import get_config
from portfolio.db import DEFAULT_GROUP, GROUPS, KINDS, Repository, kind_of, person_of
from portfolio.logs import get_logger, log

logger = get_logger(__name__)


def build_report(user_id: str, holdings: list[dict[str, Any]], repo: Repository, now: datetime) -> str | None:
    since = (now.date() - timedelta(days=14)).isoformat()
    histories = {h["ticker"]: repo.price_history(h["ticker"], since) for h in holdings}
    latest = {t: h[-1][1] for t, h in histories.items() if h}
    week_ago = {t: h[-6][1] for t, h in histories.items() if len(h) >= 6}  # 5 trading days back
    if not latest:
        return None
    current = analytics.summarize(holdings, latest, week_ago)
    movers = []
    for position in current["positions"]:
        start = week_ago.get(position["ticker"])
        if position["price"] is not None and start and all(t != position["ticker"] for _, t in movers):
            movers.append((position["price"] / start - 1.0, position["ticker"]))
    movers.sort(reverse=True)

    lines = [
        "Weekly portfolio summary",
        "",
        f"Total value: ${current['total_value']:,.2f}",
    ]
    if current["day_change_pct"] is not None:
        lines.append(f"Change this week: ${current['day_change']:+,.2f} ({current['day_change_pct'] * 100:+.2f}%)")
    if current["total_gain_pct"] is not None:
        lines.append(f"All-time gain: ${current['total_gain']:+,.2f} ({current['total_gain_pct'] * 100:+.2f}%)")
    if movers:
        best, worst = movers[0], movers[-1]
        lines += ["", f"Best this week: {best[1]} {best[0] * 100:+.2f}%", f"Worst this week: {worst[1]} {worst[0] * 100:+.2f}%"]
    lines += _breakdown(holdings, latest, week_ago)
    lines += ["", "Allocation:"]
    for ticker, weight in sorted(analytics.combined_weights(holdings, latest).items(), key=lambda kv: -kv[1]):
        lines.append(f"  {ticker:<6} {weight * 100:5.1f}%")

    cutoff = (now - timedelta(days=7)).isoformat(timespec="seconds")
    alerts = [a for a in repo.list_alerts(user_id) if a["created_at"] >= cutoff]
    lines += ["", "Alerts this week:" if alerts else "No alerts fired this week."]
    lines += [f"  - {a['message']}" for a in alerts]
    return "\n".join(lines)


def _breakdown(holdings: list[dict[str, Any]], latest: dict[str, float], week_ago: dict[str, float]) -> list[str]:
    """Value per person (when both have holdings) and per group (when more than one is used)."""
    cfg = get_config()
    names = {"mine": cfg.owner_name, "partner": cfg.partner_name}

    def group(holding: dict[str, Any]) -> str:
        return holding.get("group", DEFAULT_GROUP)

    present = [g for g in GROUPS if any(group(h) == g for h in holdings)]
    people = [p for p in names if any(person_of(g) == p for g in present)]

    def line(label: str, subset: list[dict[str, Any]]) -> str:
        part = analytics.summarize(subset, latest, week_ago)
        change = f" ({part['day_change_pct'] * 100:+.2f}% this week)" if part["day_change_pct"] is not None else ""
        return f"  {label}: ${part['total_value']:,.2f}{change}"

    lines: list[str] = []
    if len(people) > 1:
        lines += ["", "By person:"]
        lines += [line(names[p], [h for h in holdings if person_of(group(h)) == p]) for p in people]
    if len(present) > 1:
        lines += ["", "By group:"]
        for g in present:
            label = KINDS[kind_of(g)]
            if len(people) > 1:
                label = f"{names[person_of(g)]} · {label}"
            lines.append(line(label, [h for h in holdings if group(h) == g]))
    return lines


def handler(event: dict[str, Any] | None, context: Any, repo: Repository | None = None) -> dict[str, Any]:
    repo = repo or Repository()
    now = datetime.now(UTC)
    by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for holding in repo.all_holdings():
        by_user[holding["user_id"]].append(holding)
    sent = 0
    for user_id, holdings in by_user.items():
        report = build_report(user_id, holdings, repo, now)
        if report:
            notify.send_email(f"Your weekly portfolio summary, {now:%b %d}", report)
            sent += 1
    log(logger, "weekly_report_complete", reports=sent)
    return {"reports_sent": sent}
