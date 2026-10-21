"""send_report(report, recipients): the only function that sends email (design rule 2).

Gmail SMTP with STARTTLS and an App Password (GMAIL_ADDRESS, GMAIL_APP_PASSWORD in
.env). The message layout is what Gmail renders reliably:

    multipart/mixed
      multipart/alternative
        text/plain              short fallback built from Report.numbers
        multipart/related
          text/html             the report, images referenced as cid:<chart name>
          image/png ...         one inline part per chart (Gmail blocks data: URIs)
      application/vnd...sheet   the Excel file, when the report has one

Every recipient must be on delivery.allowed_recipients in config/reports.yaml;
otherwise nothing is sent. Failure notices are Reports too, so they go through the
same function.
"""

import html
import logging
import os
import smtplib
import ssl
from datetime import date
from email.message import EmailMessage
from pathlib import Path

from src.reports import builder
from src.reports.builder import Report
from src.reports.delivery_config import ConfigError, delivery_settings

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587  # STARTTLS
SMTP_TIMEOUT_SECONDS = 30
ADDRESS_ENV = "GMAIL_ADDRESS"
PASSWORD_ENV = "GMAIL_APP_PASSWORD"
EXCEL_SUBTYPE = "vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_ERROR_CHARS = 2_000  # keep failure notices short
SETUP_HINT = "See 'Gmail setup' in README.md (2-Step Verification, then an App Password)."


class EmailConfigError(ConfigError):
    """Gmail credentials are missing or rejected; retrying will not help."""


class RecipientNotAllowedError(ConfigError):
    """A recipient is not on the allowlist; the whole send was refused."""


# ---------------------------------------------------------------- sending


def send_report(
    report: Report, recipients: list[str], *, config_path: Path = builder.CONFIG_PATH
) -> None:
    """Email a built report to recipients, all of whom must be on the allowlist.

    Raises RecipientNotAllowedError or EmailConfigError (not worth retrying), or an
    smtplib/OSError for network trouble (worth retrying).
    """
    allowed = delivery_settings(builder.read_config_file(config_path)).allowed_recipients
    to = check_recipients(recipients, allowed)
    sender, password = gmail_credentials()
    message = build_message(report, sender, to)
    deliver(message, sender, password, to)
    logger.info("Sent %s to %d recipient(s)", report.name, len(to))


def check_recipients(recipients: list[str], allowed: frozenset[str]) -> list[str]:
    """Return the lower-cased recipients, or raise if any is not allowed."""
    to = [r.strip().lower() for r in recipients]
    if not to:
        raise RecipientNotAllowedError("No recipients given; nothing was sent.")
    refused = [r for r in to if r not in allowed]
    if refused:
        for address in refused:
            logger.warning("Refused recipient not on the allowlist: %s", address)
        raise RecipientNotAllowedError(
            f"{len(refused)} recipient(s) not on delivery.allowed_recipients in "
            "config/reports.yaml; nothing was sent."
        )
    return to


def gmail_credentials() -> tuple[str, str]:
    """GMAIL_ADDRESS and GMAIL_APP_PASSWORD, or a clear error naming what is missing."""
    sender = os.environ.get(ADDRESS_ENV, "").strip()
    # Google shows App Passwords in groups of four ("abcd efgh ..."); spaces are not part of it.
    password = os.environ.get(PASSWORD_ENV, "").replace(" ", "").strip()
    missing = [
        name for name, value in ((ADDRESS_ENV, sender), (PASSWORD_ENV, password)) if not value
    ]
    if missing:
        raise EmailConfigError(f"{' and '.join(missing)} not set in .env. {SETUP_HINT}")
    return sender, password


def deliver(message: EmailMessage, sender: str, password: str, to: list[str]) -> None:
    """Send over Gmail SMTP: connect, upgrade to TLS, log in, send."""
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        try:
            smtp.login(sender, password)
        except smtplib.SMTPAuthenticationError as error:
            raise EmailConfigError(
                f"Gmail rejected the login for {sender}. {SETUP_HINT}"
            ) from error
        smtp.send_message(message, from_addr=sender, to_addrs=to)


# ---------------------------------------------------------------- message


def build_message(report: Report, sender: str, to: list[str]) -> EmailMessage:
    """The MIME message: text + HTML alternatives, inline chart images, Excel attachment."""
    message = EmailMessage()
    message["Subject"] = report.subject
    message["From"] = sender
    message["To"] = ", ".join(to)
    message.set_content(plain_text(report))
    message.add_alternative(report.html, subtype="html")
    html_part = message.get_payload()[1]
    for cid, png in report.images.items():
        # add_related turns the HTML part into multipart/related; the cid matches
        # the template's <img src="cid:..."> (angle brackets are part of the header).
        html_part.add_related(png, "image", "png", cid=f"<{cid}>", disposition="inline")
    if report.excel_path:
        message.add_attachment(
            report.excel_path.read_bytes(),
            maintype="application",
            subtype=EXCEL_SUBTYPE,
            filename=report.excel_path.name,
        )
    return message


def plain_text(report: Report) -> str:
    """Text fallback: the period, the summary and headline KPIs, formatted as in the HTML."""
    numbers = report.numbers
    if "text" in numbers:  # failure notices carry their own text
        return numbers["text"]
    period = numbers["period"]
    lines = [numbers["title"], period["label"], f"Covers {period['start']} to {period['end']}"]
    if numbers.get("data_end"):
        lines[-1] += f" (data available through {numbers['data_end']})"
    lines += ["", numbers.get("summary") or builder.SUMMARY_PLACEHOLDER, ""]
    headline = next((s for s in numbers["sections"].values() if s["type"] == "kpis"), None)
    if headline:
        comparisons = numbers["comparisons"]
        for row in headline["rows"]:
            fmt = builder.FORMATTERS[row["kind"]]
            changes = [
                f"{builder.fmt_change(row['comparisons'][name]['change'], builder.change_unit(row['kind']))}"
                f" vs {comparisons[name]['name']}"
                for name in comparisons
            ]
            lines.append(f"{row['label']}: {fmt(row['current'])} ({', '.join(changes)})")
    lines += ["", "Open this email in HTML view for the charts and full tables."]
    if report.excel_path:
        lines.append(f"Detail tables are attached: {report.excel_path.name}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- failure notice


def failure_report(report_name: str, as_of: date | None, error: BaseException) -> Report:
    """A short 'report failed' notice, shaped as a Report so send_report can send it."""
    detail = f"{type(error).__name__}: {error}"[:MAX_ERROR_CHARS]
    when = as_of.isoformat() if as_of else "unknown period"
    subject = f"FAILED: {report_name} ({when})"
    text = (
        f"The scheduled report {report_name} for {when} failed after its retry.\n\n"
        f"Error:\n{detail}\n\n"
        "Details are in outputs/logs/scheduler.log and the report_runs table in "
        "data/audit.duckdb.\n"
    )
    body = (
        '<html><body style="font-family:Arial,Helvetica,sans-serif;font-size:14px;">'
        f"<p>The scheduled report <b>{html.escape(report_name)}</b> for {html.escape(when)} "
        "failed after its retry.</p>"
        f'<pre style="white-space:pre-wrap;">{html.escape(detail)}</pre>'
        "<p>Details are in outputs/logs/scheduler.log and the report_runs table in "
        "data/audit.duckdb.</p></body></html>"
    )
    return Report(
        name=f"{report_name}_failure",
        title=subject,
        as_of=as_of or date.today(),
        subject=subject,
        html=body,
        images={},
        excel_path=None,
        numbers={"report": report_name, "text": text},
        output_dir=builder.OUTPUT_ROOT,
    )
