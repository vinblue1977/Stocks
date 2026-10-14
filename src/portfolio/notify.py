"""Email notifications through the SNS alerts topic."""

from __future__ import annotations

from email.message import EmailMessage
from functools import lru_cache
from typing import Any

import boto3

from portfolio.config import get_config
from portfolio.logs import get_logger

logger = get_logger(__name__)

DISCLAIMER = (
    "\n\n--\nThis is an automated alert based on rules you set in Portfolio Tracker. "
    "It describes market data and is not investment advice."
)


@lru_cache(maxsize=1)
def _sns() -> Any:
    return boto3.client("sns")


@lru_cache(maxsize=1)
def _ses() -> Any:
    return boto3.client("sesv2")


def send_email(subject: str, body: str) -> None:
    topic_arn = get_config().topic_arn
    if not topic_arn:
        return
    _sns().publish(TopicArn=topic_arn, Subject=subject[:100], Message=body + DISCLAIMER)


def send_email_with_pdf(subject: str, body: str, filename: str, pdf: bytes) -> None:
    """Email with a PDF attached, through SES (SNS can't attach files). From and to the verified report address."""
    address = get_config().report_email
    if not address:
        raise RuntimeError("REPORT_EMAIL is not set")
    message = EmailMessage()
    message["Subject"] = subject[:100]
    message["From"] = f"Portfolio Tracker <{address}>"
    message["To"] = address
    message.set_content(body + DISCLAIMER.replace("an automated alert based on rules you set", "your automated monthly checkup"))
    message.add_attachment(pdf, maintype="application", subtype="pdf", filename=filename)
    _ses().send_email(Content={"Raw": {"Data": message.as_bytes()}})


def push_to_user(repo: Any, user_id: str, title: str, body: str, url: str = "/#/alerts") -> int:
    """Send a notification to every phone the user turned notifications on for; drops expired ones."""
    from portfolio import webpush  # only loaded where notifications are sent
    from portfolio.secrets import get_secret

    subscriptions = repo.list_push(user_id)
    if not subscriptions:
        return 0
    cfg = get_config()
    key = get_secret(cfg.vapid_key_param)
    sent = 0
    for sub in subscriptions:
        try:
            webpush.send(sub, {"title": title, "body": body, "url": url}, key, cfg.vapid_subject)
            sent += 1
        except webpush.Gone:
            repo.delete_push(user_id, sub["endpoint"])  # the phone turned it off or the app was removed
        except Exception:  # one phone failing must not stop the others or the alert email
            logger.exception("push failed")
    return sent
