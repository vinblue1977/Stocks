"""Sunday email: the week's headline numbers and a link to the dashboard for the detail."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from portfolio import analytics, checkup, notify
from portfolio.config import get_config
from portfolio.db import Repository
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
    weights = analytics.combined_weights(holdings, latest)
    limit = checkup.holding_limit(repo.get_targets(user_id))
    if over := checkup.over_limit(weights, limit):
        lines += ["", f"Over your {limit}% single-holding limit: {', '.join(o['ticker'] for o in over)}"]

    cutoff = (now - timedelta(days=7)).isoformat(timespec="seconds")
    alerts = [a for a in repo.list_alerts(user_id) if a["created_at"] >= cutoff]
    lines += ["", f"Alerts fired this week: {len(alerts)}" if alerts else "No alerts fired this week."]
    if site := get_config().site_url:
        lines += ["", f"Full detail: {site}/#/"]
    return "\n".join(lines)


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
