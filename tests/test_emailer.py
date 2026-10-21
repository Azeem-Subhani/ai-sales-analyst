"""Emailer tests (plan sections 8 and 13). SMTP is replaced by a fake: no real email is sent."""

import re
import smtplib
from datetime import date
from email import message_from_bytes, policy
from email.message import EmailMessage
from pathlib import Path

import pytest

from src.reports import builder, emailer
from src.reports.builder import Report
from tests.conftest import OWNER, FakeSMTP

CID_REF = re.compile(r'src="cid:([^"]+)"')


@pytest.fixture(scope="module")
def reports(built: dict, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Report]:
    """Weekly (charts + Excel) and daily (neither) reports from the generated database."""
    out = tmp_path_factory.mktemp("email_reports")
    return {
        "weekly": builder.build_report(
            "weekly_sales_review",
            date(2026, 9, 27),
            db_path=built["db_path"],
            output_root=out,
            data_end=date(2026, 9, 30),
        ),
        "daily": builder.build_report(
            "daily_sales_snapshot", date(2026, 9, 30), db_path=built["db_path"], output_root=out
        ),
    }


def sent_message(smtp: type[FakeSMTP]) -> EmailMessage:
    """The single message sent, re-parsed from bytes as a mail client would see it."""
    assert len(smtp.instances) == 1 and len(smtp.instances[0].sent) == 1
    msg, _, _ = smtp.instances[0].sent[0]
    return message_from_bytes(msg.as_bytes(), policy=policy.default)


def test_weekly_message_structure(smtp: type[FakeSMTP], reports: dict[str, Report]) -> None:
    report = reports["weekly"]
    emailer.send_report(report, [OWNER])

    server = smtp.instances[0]
    assert (server.host, server.port) == ("smtp.gmail.com", 587)
    assert server.calls.index("starttls") < server.calls.index("login") < server.calls.index("send")
    assert server.sent[0][2] == [OWNER]

    msg = sent_message(smtp)
    assert msg["Subject"] == report.subject
    assert msg["To"] == OWNER
    assert msg.get_content_type() == "multipart/mixed"

    text = msg.get_body(preferencelist=("plain",))
    assert text is not None and report.numbers["period"]["label"] in text.get_content()

    html_part = msg.get_body(preferencelist=("html",))
    cids_in_html = set(CID_REF.findall(html_part.get_content()))
    assert cids_in_html == set(report.images)

    images = [p for p in msg.walk() if p.get_content_type() == "image/png"]
    assert {p["Content-ID"].strip("<>") for p in images} == cids_in_html
    for part in images:
        assert part.get_content_disposition() == "inline"
        assert part.get_content() == report.images[part["Content-ID"].strip("<>")]

    attachments = list(msg.iter_attachments())
    assert [a.get_filename() for a in attachments] == [report.excel_path.name]
    assert attachments[0].get_content() == report.excel_path.read_bytes()


def test_email_states_period_covered(smtp: type[FakeSMTP], reports: dict[str, Report]) -> None:
    emailer.send_report(reports["weekly"], [OWNER])
    msg = sent_message(smtp)
    for body in (msg.get_body(("plain",)).get_content(), msg.get_body(("html",)).get_content()):
        assert "Covers 2026-09-21 to 2026-09-27" in body
        assert "data available through 2026-09-30" in body


def test_daily_has_no_attachment_or_images(
    smtp: type[FakeSMTP], reports: dict[str, Report]
) -> None:
    emailer.send_report(reports["daily"], [OWNER])
    msg = sent_message(smtp)
    assert list(msg.iter_attachments()) == []
    assert not [p for p in msg.walk() if p.get_content_maintype() == "image"]
    assert msg.get_body(("plain",)) is not None


def test_recipient_not_on_allowlist_refuses_whole_send(
    smtp: type[FakeSMTP], reports: dict[str, Report], caplog: pytest.LogCaptureFixture
) -> None:
    with pytest.raises(emailer.RecipientNotAllowedError):
        emailer.send_report(reports["weekly"], [OWNER, "stranger@example.com"])
    assert smtp.instances == []  # never even connected
    assert "stranger@example.com" in caplog.text


def test_allowlist_is_case_insensitive(smtp: type[FakeSMTP], reports: dict[str, Report]) -> None:
    emailer.send_report(reports["daily"], [OWNER.upper()])
    assert len(smtp.instances[0].sent) == 1


@pytest.mark.parametrize("missing", ["GMAIL_APP_PASSWORD", "GMAIL_ADDRESS"])
def test_missing_credentials_give_clear_message(
    smtp: type[FakeSMTP], reports: dict[str, Report], monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.delenv(missing)
    with pytest.raises(emailer.EmailConfigError, match=f"{missing} not set in .env.*README"):
        emailer.send_report(reports["daily"], [OWNER])
    assert smtp.instances == []


def test_rejected_login_is_a_config_error(
    smtp: type[FakeSMTP], reports: dict[str, Report], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(self: FakeSMTP, user: str, password: str) -> None:
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(smtp, "login", refuse)  # the class pytest loaded
    with pytest.raises(emailer.EmailConfigError, match="rejected the login"):
        emailer.send_report(reports["daily"], [OWNER])


def test_unset_owner_variable_is_a_config_error(
    smtp: type[FakeSMTP], reports: dict[str, Report], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REPORT_OWNER_EMAIL")
    with pytest.raises(emailer.ConfigError, match="REPORT_OWNER_EMAIL is not set"):
        emailer.send_report(reports["daily"], [OWNER])


def test_failure_notice_goes_through_send_report(smtp: type[FakeSMTP], tmp_path: Path) -> None:
    notice = emailer.failure_report(
        "weekly_sales_review", date(2026, 9, 27), RuntimeError("boom <x>")
    )
    emailer.send_report(notice, [OWNER])
    msg = sent_message(smtp)
    assert msg["Subject"] == "FAILED: weekly_sales_review (2026-09-27)"
    assert "RuntimeError: boom <x>" in msg.get_body(("plain",)).get_content()
    assert "boom &lt;x&gt;" in msg.get_body(("html",)).get_content()
