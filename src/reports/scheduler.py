"""Runs the reports in config/reports.yaml on schedule and delivers them (plan section 8).

One run: pick the period with the data clock, build the report (dry-run files are
always saved), email it when REPORT_MODE=send, and write the attempt to report_runs.
A failure is retried once after REPORT_RETRY_DELAY_SECONDS; if the retry fails too,
a failure notice goes to delivery.failure_recipients. Config problems (allowlist,
Gmail credentials, unset ${VAR}) are not retried, because retrying cannot fix them.
"""

import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from src.analytics import kpis
from src.reports import audit, builder, data_clock, emailer
from src.reports.delivery_config import ConfigError, delivery_settings, report_recipients

logger = logging.getLogger(__name__)

MODE_ENV = "REPORT_MODE"
MODES = ("dry_run", "send")
DEFAULT_MODE = "dry_run"  # safe default: nothing leaves the machine
RETRY_DELAY_ENV = "REPORT_RETRY_DELAY_SECONDS"
DEFAULT_RETRY_DELAY_SECONDS = 300  # plan section 8: retry once after 5 minutes
MAX_ATTEMPTS = 2  # the first try plus one retry

# matplotlib's pyplot is not thread-safe and scheduler jobs run in threads (weekly and
# monthly both fire at 08:00 when the 1st is a Monday), so builds take turns.
_build_lock = threading.Lock()


@dataclass
class Settings:
    """Everything a run needs, read from .env once; tests pass their own paths."""

    mode: str = DEFAULT_MODE
    timezone: str = data_clock.DEFAULT_TIMEZONE
    data_end: date | None = None
    retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS
    config_path: Path = builder.CONFIG_PATH
    db_path: Path = kpis.DB_PATH
    output_root: Path = builder.OUTPUT_ROOT
    audit_path: Path = audit.AUDIT_DB_PATH
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)


def settings_from_env() -> Settings:
    """Read REPORT_MODE, TIMEZONE, DATA_END_DATE and REPORT_RETRY_DELAY_SECONDS."""
    mode = os.environ.get(MODE_ENV, "").strip() or DEFAULT_MODE
    if mode not in MODES:
        raise ConfigError(f"{MODE_ENV} must be one of {MODES}; got {mode!r}.")
    delay = os.environ.get(RETRY_DELAY_ENV, "").strip() or str(DEFAULT_RETRY_DELAY_SECONDS)
    try:
        retry_delay = float(delay)
    except ValueError as error:
        raise ConfigError(
            f"{RETRY_DELAY_ENV} must be a number of seconds; got {delay!r}."
        ) from error
    try:
        data_end = data_clock.data_end_from_env()
    except ValueError as error:
        raise ConfigError(str(error)) from error
    return Settings(
        mode=mode,
        timezone=data_clock.timezone_name(),
        data_end=data_end,
        retry_delay_seconds=retry_delay,
    )


# ---------------------------------------------------------------- one run


def run_report(name: str, settings: Settings, triggered_by: str, today: date | None = None) -> bool:
    """Build and deliver one report with one retry. Returns True on success.

    Raises ConfigError only for problems found before the first attempt (unknown
    report); everything later is logged, audited and reported by failure notice.
    """
    config = builder.load_config(settings.config_path)
    if name not in config:
        raise ConfigError(f"Unknown report {name!r}; expected one of {sorted(config)}.")
    block = config[name]
    today = today or data_clock.today_in(settings.timezone)
    as_of = data_clock.reporting_as_of(block["period"], today, settings.data_end)
    period = kpis.period_for(block["period"], as_of)
    run_id = uuid.uuid4().hex
    logger.info(
        "Run %s: %s for %s (%s, %s)", run_id[:8], name, period.label, settings.mode, triggered_by
    )

    error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        started, clock = datetime.now(UTC), time.perf_counter()
        recipients = list(block.get("recipients") or [])  # unexpanded until send mode needs them
        try:
            if settings.mode == "send":
                recipients = report_recipients(block)
            deliver_once(name, as_of, recipients, settings)
            error = None
        except Exception as exc:  # noqa: BLE001 - every failure is audited and reported
            error = exc
        audit.record_run(
            audit.RunRecord(
                run_id=run_id,
                report=name,
                attempt=attempt,
                triggered_by=triggered_by,
                mode=settings.mode,
                as_of=as_of,
                period_start=period.start,
                period_end=period.end,
                recipients=", ".join(recipients),
                status="success" if error is None else "failed",
                error=None if error is None else f"{type(error).__name__}: {error}",
                duration_seconds=round(time.perf_counter() - clock, 3),
                started_at_utc=started.replace(tzinfo=None),
                finished_at_utc=datetime.now(UTC).replace(tzinfo=None),
            ),
            settings.audit_path,
        )
        if error is None:
            logger.info("Run %s: %s succeeded on attempt %d", run_id[:8], name, attempt)
            return True
        logger.error("Run %s: %s attempt %d failed: %s", run_id[:8], name, attempt, error)
        if isinstance(error, ConfigError) or attempt == MAX_ATTEMPTS:
            break
        logger.info("Retrying %s in %s seconds", name, settings.retry_delay_seconds)
        settings.sleep(settings.retry_delay_seconds)

    notify_failure(name, as_of, error, settings)
    return False


def deliver_once(name: str, as_of: date, recipients: list[str], settings: Settings) -> None:
    """One attempt: build (always saving dry-run files), then email in send mode."""
    with _build_lock:
        report = builder.build_report(
            name,
            as_of,
            db_path=settings.db_path,
            output_root=settings.output_root,
            config_path=settings.config_path,
            data_end=settings.data_end,
        )
    if settings.mode == "send":
        emailer.send_report(report, recipients, config_path=settings.config_path)
    else:
        logger.info("dry_run: saved %s, nothing sent", report.output_dir)


def notify_failure(name: str, as_of: date, error: Exception, settings: Settings) -> None:
    """Email the failure notice in send mode; never raises (it is the last resort)."""
    if settings.mode != "send":
        logger.warning("dry_run: failure notice for %s not emailed", name)
        return
    try:
        delivery = delivery_settings(builder.read_config_file(settings.config_path))
        notice = emailer.failure_report(name, as_of, error)
        emailer.send_report(notice, delivery.failure_recipients, config_path=settings.config_path)
        logger.info("Failure notice for %s sent", name)
    except Exception as notice_error:  # noqa: BLE001 - logged; nothing else can be done
        logger.error("Could not send the failure notice for %s: %s", name, notice_error)
