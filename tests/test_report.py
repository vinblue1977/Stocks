import email
from datetime import UTC, date, datetime, timedelta

from botocore.exceptions import ClientError
from conftest import price_series

from portfolio import checkup, notify, report_pdf
from portfolio.handlers import monthly_checkup

TARGETS = {
    "areas": {"us": {"pct": 60, "fund": "VTI"}, "intl": {"pct": 20, "fund": "VXUS"}, "sector": {"pct": 20, "fund": "SMH"}},
    "sector_ceiling": 20,
    "overrides": {},
}


def built_for(repo):
    start = (date.today() - timedelta(days=500)).isoformat()
    for user_group, ticker, shares in (("stocks", "VTI", 6), ("stocks", "SMH", 4), ("partner_auto", "VTI", 2)):
        repo.put_holding("u1", {"ticker": ticker, "group": user_group, "shares": shares, "avg_cost": 1})
    for ticker in ("VTI", "SMH"):
        repo.put_prices(ticker, price_series(100, 0.3, 360, start))
    return checkup.build(repo, repo.all_holdings(), TARGETS)


def test_pdf_has_three_pages(repo):
    built = built_for(repo)
    assert len(built["views"]["household"]["history_1y"]) > 200 and built["views"]["household"]["holdings"][0]["area"]
    pdf = report_pdf.build_pdf(
        built, TARGETS, "Value is $5,000.\nSemis <b>are</b> 40% & up.", {"mine": "Ana", "partner": "Ben"}, "May 2026"
    )
    assert pdf.startswith(b"%PDF") and pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page ") >= 3


def test_pdf_without_targets_still_builds(repo):
    built = built_for(repo)
    assert report_pdf.build_pdf(built, None, "Only one line.", {}, "May 2026").startswith(b"%PDF")


def test_email_with_pdf_is_a_mime_attachment(monkeypatch):
    sent = []

    class FakeSes:
        def send_email(self, **kwargs):
            sent.append(kwargs)

    monkeypatch.setattr(notify, "_ses", lambda: FakeSes())
    monkeypatch.setattr(notify, "get_config", lambda: type("C", (), {"report_email": "me@example.com"})())
    notify.send_email_with_pdf("Checkup", "Short body.", "Checkup-2026-05.pdf", b"%PDF-1.4 test")
    message = email.message_from_bytes(sent[0]["Content"]["Raw"]["Data"])
    assert message["To"] == "me@example.com" and "me@example.com" in message["From"]
    parts = {p.get_content_type(): p for p in message.walk()}
    assert parts["application/pdf"].get_filename() == "Checkup-2026-05.pdf"
    assert parts["application/pdf"].get_payload(decode=True) == b"%PDF-1.4 test"
    assert "Short body." in parts["text/plain"].get_payload(decode=True).decode()


def test_deliver_sends_pdf_and_falls_back_to_text(repo, monkeypatch):
    built = built_for(repo)
    now = datetime(2026, 11, 7, 14, tzinfo=UTC)
    pdfs, texts = [], []
    monkeypatch.setattr(monthly_checkup.notify, "send_email_with_pdf", lambda s, b, f, p: pdfs.append((b, f, p)))
    monkeypatch.setattr(monthly_checkup.notify, "send_email", lambda s, b: texts.append(b))
    cfg = monthly_checkup.get_config()
    monkeypatch.setattr(monthly_checkup, "get_config", lambda: type(cfg)(**{**cfg.__dict__, "report_email": "me@example.com"}))
    monthly_checkup.deliver(built, TARGETS, "One.\nTwo.", now)
    body, filename, pdf = pdfs[0]
    assert filename == "Checkup-2026-11.pdf" and pdf.startswith(b"%PDF") and "attached as a PDF" in body and not texts

    def broken(*args):
        raise ClientError({"Error": {"Code": "MessageRejected", "Message": "Email address is not verified."}}, "SendEmail")

    monkeypatch.setattr(monthly_checkup.notify, "send_email_with_pdf", broken)
    monthly_checkup.deliver(built, TARGETS, "One.\nTwo.", now)
    assert "YOUR MIX VS. TARGET" in texts[0]


def test_minus_signs_stay_plain():
    assert report_pdf._spct(-0.12) == "-12%" and report_pdf._escape("−5% → ≥ 3 <b>") == "-5% -&gt; &gt;= 3 &lt;b&gt;"


def test_timeout_after_sending_does_not_send_twice(repo, monkeypatch):
    import pytest
    from botocore.exceptions import ReadTimeoutError

    built = built_for(repo)
    texts = []
    cfg = monthly_checkup.get_config()
    monkeypatch.setattr(monthly_checkup, "get_config", lambda: type(cfg)(**{**cfg.__dict__, "report_email": "me@example.com"}))
    monkeypatch.setattr(monthly_checkup.notify, "send_email", lambda s, b: texts.append(b))

    def timeout(*args):
        raise ReadTimeoutError(endpoint_url="https://email.us-east-2.amazonaws.com")

    monkeypatch.setattr(monthly_checkup.notify, "send_email_with_pdf", timeout)
    with pytest.raises(ReadTimeoutError):
        monthly_checkup.deliver(built, TARGETS, "One.\nTwo.", datetime(2026, 11, 7, tzinfo=UTC))
    assert texts == []
