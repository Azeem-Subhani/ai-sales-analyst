"""Builds every report the scheduler would have sent between two run dates (Week 6).

Instead of leaving the scheduler running for two weeks, this replays its schedule:
for each run date it works out which reports fire (the scheduler's own cron
triggers from config/reports.yaml) and which period each covers (the data clock,
as if "today" were that run date), then builds the report as a dry run.

It never emails, whatever REPORT_MODE says, and never writes report_runs (that table
is the record of real runs). Instead it writes outputs/backfill/manifest.csv and
outputs/backfill/index.html. One failed report is recorded and the rest still run.

    python -m src.reports.backfill --from 2026-09-14 --to 2026-10-01 [--no-summary]
"""

import argparse
import csv
import logging
import sys
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.triggers.base import BaseTrigger
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from src.analytics import kpis
from src.reports import builder, data_clock, scheduler
from src.reports.delivery_config import ConfigError

logger = logging.getLogger(__name__)

BACKFILL_ROOT = builder.PROJECT_ROOT / "outputs" / "backfill"
MANIFEST_NAME = "manifest.csv"
INDEX_NAME = "index.html"
INDEX_TEMPLATE = "backfill_index.html"
MANIFEST_COLUMNS = [
    "run_date",
    "report",
    "as_of",
    "period",
    "status",
    "summary_status",
    "summary_retry_reason",
    "summary_cost_usd",
    "output_folder",
    "error",
]
NEXT_FIRE_STEP = timedelta(seconds=1)  # ask the trigger for the fire time after this one


@dataclass(frozen=True)
class PlannedRun:
    """One report the scheduler would have built on run_date."""

    run_date: date
    report: str
    as_of: date
    period: kpis.Period


# ---------------------------------------------------------------- which reports fire when


def fire_dates(trigger: BaseTrigger, start: date, end: date, tz_name: str) -> list[date]:
    """Dates (inclusive range) on which the trigger fires, in the scheduler's time zone."""
    zone = ZoneInfo(tz_name)
    now = datetime.combine(start, time.min, zone)
    stop = datetime.combine(end + timedelta(days=1), time.min, zone)
    dates: list[date] = []
    previous = None
    while True:
        fire = trigger.get_next_fire_time(previous, now)
        if fire is None or fire >= stop:
            return dates
        dates.append(fire.date())
        previous, now = fire, fire + NEXT_FIRE_STEP


def plan_runs(settings: scheduler.Settings, start: date, end: date) -> list[PlannedRun]:
    """Every report that fires from start to end, by run date then config order."""
    if end < start:
        raise ConfigError(f"--to {end} is before --from {start}.")
    config = builder.load_config(settings.config_path)
    order = {name: index for index, name in enumerate(config)}
    runs = []
    for spec in scheduler.job_specs(settings):
        kind = config[spec.name]["period"]
        for run_date in fire_dates(spec.trigger, start, end, settings.timezone):
            as_of = data_clock.reporting_as_of(kind, run_date, settings.data_end)
            runs.append(PlannedRun(run_date, spec.name, as_of, kpis.period_for(kind, as_of)))
    return sorted(runs, key=lambda r: (r.run_date, order[r.report]))


# ---------------------------------------------------------------- building


def build_run(run: PlannedRun, settings: scheduler.Settings, root: Path) -> dict:
    """Build one planned report as a dry run; never raises, the manifest records errors."""
    # Forced dry run: only the build step is used, never deliver_once or send_report.
    run_settings = replace(settings, mode="dry_run", output_root=root / run.run_date.isoformat())
    row = {
        "run_date": run.run_date.isoformat(),
        "report": run.report,
        "as_of": run.as_of.isoformat(),
        "period": run.period.label,
        "status": "ok",
        "summary_status": "",
        "summary_retry_reason": "",
        "summary_cost_usd": "",
        "output_folder": "",
        "error": "",
    }
    try:
        report = scheduler.build_once(run.report, run.as_of, run_settings)
    except Exception as error:
        logger.exception("Backfill of %s for %s failed", run.report, run.run_date)
        row.update(status="failed", error=f"{type(error).__name__}: {error}")
        return row
    row.update(
        summary_status=report.summary_status or "",
        summary_retry_reason=report.summary_retry_reason or "",
        summary_cost_usd="" if report.summary_cost_usd is None else report.summary_cost_usd,
        output_folder=report.output_dir.relative_to(root).as_posix(),
    )
    return row


def write_manifest(rows: list[dict], root: Path) -> Path:
    """manifest.csv: one line per planned report, in run order."""
    path = root / MANIFEST_NAME
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_index(rows: list[dict], root: Path, start: date, end: date) -> Path:
    """index.html: a table linking to every backfilled report.html."""
    env = Environment(
        loader=FileSystemLoader(builder.TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
    )
    html = env.get_template(INDEX_TEMPLATE).render(
        rows=rows, start=start.isoformat(), end=end.isoformat(), report_file=builder.HTML_FILENAME
    )
    path = root / INDEX_NAME
    path.write_text(html, encoding="utf-8")
    return path


def backfill(
    settings: scheduler.Settings, start: date, end: date, root: Path = BACKFILL_ROOT
) -> list[dict]:
    """Build every planned report, then write the manifest and the index."""
    runs = plan_runs(settings, start, end)
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for number, run in enumerate(runs, start=1):
        logger.info(
            "Backfill %d/%d: %s on %s (as of %s)",
            number,
            len(runs),
            run.report,
            run.run_date,
            run.as_of,
        )
        rows.append(build_run(run, settings, root))
    write_manifest(rows, root)
    write_index(rows, root, start, end)
    return rows


# ---------------------------------------------------------------- command line


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description="Build the scheduled reports for past run dates.")
    parser.add_argument("--from", dest="start", required=True, type=date.fromisoformat)
    parser.add_argument("--to", dest="end", required=True, type=date.fromisoformat)
    parser.add_argument("--no-summary", action="store_true", help="skip the LLM summaries")
    return parser.parse_args(argv)


def print_summary(rows: list[dict], root: Path) -> None:
    """Counts per report and status, failures, and the summary spend."""
    by_report: dict[str, int] = {}
    for row in rows:
        by_report[row["report"]] = by_report.get(row["report"], 0) + 1
    failed = [row for row in rows if row["status"] != "ok"]
    spend = sum(float(row["summary_cost_usd"]) for row in rows if row["summary_cost_usd"] != "")
    print(f"Backfilled {len(rows)} reports into {root}")
    for name, count in by_report.items():
        print(f"  {name}: {count}")
    print(f"  failed: {len(failed)}")
    for row in failed:
        print(f"    {row['run_date']} {row['report']}: {row['error']}")
    print(f"  summaries: {status_counts(rows, 'summary_status')}")
    print(f"  first-attempt failures: {status_counts(rows, 'summary_retry_reason')}")
    print(f"  summary cost reported by OpenRouter: ${spend:.4f}")


def status_counts(rows: list[dict], column: str) -> str:
    """'ok 12, ok_after_retry 6' for the non-empty values of one manifest column."""
    counts: dict[str, int] = {}
    for row in rows:
        if row[column]:
            counts[row[column]] = counts.get(row[column], 0) + 1
    return ", ".join(f"{value} {count}" for value, count in counts.items()) or "none"


def main(argv: list[str] | None = None) -> int:
    """Run the backfill; expected problems print one clear line and return 1."""
    args = parse_args(argv)
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        settings = scheduler.settings_from_env()
        settings.summary = not args.no_summary
        scheduler.check_summary_ready(settings)
        rows = backfill(settings, args.start, args.end, BACKFILL_ROOT)
    except ConfigError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print_summary(rows, BACKFILL_ROOT)
    return 0 if all(row["status"] == "ok" for row in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
