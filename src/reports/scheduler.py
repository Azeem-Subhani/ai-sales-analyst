"""Runs the reports in config/reports.yaml on schedule and delivers them (plan section 8).

One run: pick the period with the data clock, build the report (dry-run files are
always saved), email it when REPORT_MODE=send, and write the attempt to report_runs.
A failure is retried once after REPORT_RETRY_DELAY_SECONDS; if the retry fails too,
a failure notice goes to delivery.failure_recipients. Config problems (allowlist,
Gmail credentials, unset ${VAR}) are not retried, because retrying cannot fix them.

    python -m src.reports.scheduler                       start; runs until Ctrl+C
    python -m src.reports.scheduler --list                jobs and next run times
    python -m src.reports.scheduler --run-now weekly_sales_review
    python -m src.reports.scheduler --test-every 2        testing only: all reports every 2 min
"""

import argparse
import logging
import os
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv

from src.agent import llm
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
    summary: bool = True  # False with --no-summary: no LLM call
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
        summary_status: str | None = None
        try:
            if settings.mode == "send":
                recipients = report_recipients(block)
            report = build_once(name, as_of, settings)
            summary_status = report.summary_status  # kept even if the send below fails
            deliver_once(report, recipients, settings)
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
                summary_status=summary_status,
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


def build_once(name: str, as_of: date, settings: Settings) -> builder.Report:
    """Build the report (always saving the dry-run files), one build at a time."""
    with _build_lock:
        return builder.build_report(
            name,
            as_of,
            db_path=settings.db_path,
            output_root=settings.output_root,
            config_path=settings.config_path,
            data_end=settings.data_end,
            summary=settings.summary,
        )


def deliver_once(report: builder.Report, recipients: list[str], settings: Settings) -> None:
    """Email the built report in send mode; in dry_run the saved files are the output."""
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


# ---------------------------------------------------------------- jobs

MISFIRE_GRACE_SECONDS = 15 * 60  # a laptop waking up late still sends that morning's report
LOG_DIR = builder.PROJECT_ROOT / "outputs" / "logs"
LOG_FILE = "scheduler.log"


@dataclass
class JobSpec:
    """One scheduled report: its trigger and how runs are labelled in the audit."""

    name: str
    trigger: BaseTrigger
    triggered_by: str


def job_specs(settings: Settings, test_every_minutes: int | None = None) -> list[JobSpec]:
    """One job per report: its cron schedule, or every N minutes in test mode."""
    specs = []
    for name, block in builder.load_config(settings.config_path).items():
        if test_every_minutes:
            trigger = IntervalTrigger(minutes=test_every_minutes, timezone=settings.timezone)
            specs.append(JobSpec(name, trigger, "test"))
        else:
            trigger = CronTrigger(timezone=settings.timezone, **block["schedule"])
            specs.append(JobSpec(name, trigger, "schedule"))
    return specs


def run_job(name: str, settings: Settings, triggered_by: str) -> None:
    """Scheduler entry point: run_report, never letting an error kill the scheduler."""
    try:
        run_report(name, settings, triggered_by)
    except Exception:
        logger.exception("Job %s crashed", name)


def build_scheduler(settings: Settings, specs: list[JobSpec]) -> BlockingScheduler:
    """A BlockingScheduler with one job per spec (test jobs also fire straight away)."""
    scheduler = BlockingScheduler(timezone=settings.timezone)
    for spec in specs:
        extra = {"next_run_time": datetime.now(ZoneInfo(settings.timezone))}
        scheduler.add_job(
            run_job,
            spec.trigger,
            args=[spec.name, settings, spec.triggered_by],
            id=spec.name,
            name=spec.name,
            max_instances=1,  # a slow run (e.g. waiting to retry) is never doubled up
            coalesce=True,
            misfire_grace_time=MISFIRE_GRACE_SECONDS,
            **(extra if spec.triggered_by == "test" else {}),
        )
    return scheduler


def describe_jobs(specs: list[JobSpec], now: datetime) -> list[str]:
    """One line per job with its trigger and next fire time."""
    width = max(len(spec.name) for spec in specs)
    lines = []
    for spec in specs:
        next_time = spec.trigger.get_next_fire_time(None, now)
        when = f"{next_time:%a %Y-%m-%d %H:%M %Z}" if next_time else "never"
        lines.append(f"{spec.name:<{width}}  next {when}  ({spec.trigger})")
    return lines


# ---------------------------------------------------------------- command line


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options (no option: start the scheduler and run until stopped)."""
    parser = argparse.ArgumentParser(description="Run the scheduled reports.")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--list", action="store_true", help="show jobs and next run times")
    action.add_argument("--run-now", metavar="REPORT", help="run one report once and exit")
    action.add_argument(
        "--test-every",
        type=int,
        metavar="MINUTES",
        help="testing only: run every report every N minutes",
    )
    parser.add_argument(
        "--no-summary",
        action="store_true",
        help="skip the LLM summary (no API call); use with --run-now",
    )
    return parser.parse_args(argv)


def setup_logging() -> None:
    """Log to the console and to outputs/logs/scheduler.log."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(LOG_DIR / LOG_FILE)],
    )


def check_send_ready(settings: Settings) -> None:
    """In send mode, fail at start-up (not at 08:00 Monday) if email cannot work."""
    if settings.mode != "send":
        return
    emailer.gmail_credentials()
    delivery_settings(builder.read_config_file(settings.config_path))
    for block in builder.load_config(settings.config_path).values():
        report_recipients(block)


def check_summary_ready(settings: Settings) -> None:
    """Warn once at start-up if summaries cannot work; reports still build and send."""
    if not settings.summary:
        return
    try:
        llm.llm_settings()
    except llm.LLMConfigError as error:
        logger.warning("Report summaries will be unavailable: %s", error)


def main(argv: list[str] | None = None) -> int:
    """Run the command; expected problems print one clear line and return 1."""
    args = parse_args(argv)
    load_dotenv()
    setup_logging()
    try:
        settings = settings_from_env()
        settings.summary = not args.no_summary
        if args.list:
            specs = job_specs(settings)
            print(
                f"Mode {settings.mode}, time zone {settings.timezone}, data end {settings.data_end}"
            )
            print("\n".join(describe_jobs(specs, datetime.now(ZoneInfo(settings.timezone)))))
            return 0
        check_send_ready(settings)
        check_summary_ready(settings)
        if args.run_now:
            return 0 if run_report(args.run_now, settings, "run_now") else 1
        if args.test_every is not None and args.test_every < 1:
            raise ConfigError("--test-every needs a whole number of minutes, 1 or more.")
        specs = job_specs(settings, args.test_every)
    except ConfigError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if args.test_every:
        logger.warning(
            "TEST MODE: every report runs every %d minute(s). Stop with Ctrl+C.", args.test_every
        )
    scheduler = build_scheduler(settings, specs)
    logger.info(
        "Scheduler started (%s mode, %s). Stop with Ctrl+C.", settings.mode, settings.timezone
    )
    for line in describe_jobs(specs, datetime.now(ZoneInfo(settings.timezone))):
        logger.info(line)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
