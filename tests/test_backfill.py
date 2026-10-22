"""Backfill tests: schedule to run dates, the forced dry run, and the manifest (Week 6).

Most tests replace the report build with a fake one, so they are fast and never call
the LLM. The one real build is marked slow.
"""

import csv
from datetime import date
from pathlib import Path

import pytest
import yaml

from src.reports import audit, backfill, builder, emailer, scheduler

START, END = date(2026, 9, 14), date(2026, 10, 1)
DATA_END = date(2026, 9, 30)


def settings_for(**changes: object) -> scheduler.Settings:
    """Settings like a dry-run .env with DATA_END_DATE 2026-09-30."""
    return scheduler.Settings(timezone="Europe/London", data_end=DATA_END, **changes)


def fake_build(name: str, as_of: date, settings: scheduler.Settings) -> builder.Report:
    """A built report without the build: just the output folder and a summary cost."""
    output_dir = settings.output_root / f"{name}_{as_of.isoformat()}"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / builder.HTML_FILENAME).write_text("<p>report</p>", encoding="utf-8")
    return builder.Report(
        name,
        name,
        as_of,
        name,
        "<p>report</p>",
        {},
        None,
        {},
        output_dir,
        summary_status="ok",
        summary_cost_usd=0.001,
    )


def read_manifest(root: Path) -> list[dict]:
    """manifest.csv as dicts."""
    with (root / backfill.MANIFEST_NAME).open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# ---------------------------------------------------------------- schedule to dates


def test_plan_matches_the_scheduler_for_the_two_weeks() -> None:
    runs = backfill.plan_runs(settings_for(), START, END)
    by_report: dict[str, list] = {}
    for run in runs:
        by_report.setdefault(run.report, []).append(run)
    assert {name: len(items) for name, items in by_report.items()} == {
        "daily_sales_snapshot": 14,
        "weekly_sales_review": 3,
        "monthly_business_review": 1,
    }
    weekly = [(r.run_date, r.as_of) for r in by_report["weekly_sales_review"]]
    assert weekly == [
        (date(2026, 9, 14), date(2026, 9, 13)),
        (date(2026, 9, 21), date(2026, 9, 20)),
        (date(2026, 9, 28), date(2026, 9, 27)),
    ]
    [monthly] = by_report["monthly_business_review"]
    assert (monthly.run_date, monthly.as_of, monthly.period.label) == (
        date(2026, 10, 1),
        date(2026, 9, 30),
        "September 2026",
    )
    daily = by_report["daily_sales_snapshot"]
    assert all(r.run_date.weekday() < 5 for r in daily)  # mon-fri, as in reports.yaml
    assert (daily[0].as_of, daily[-1].as_of) == (date(2026, 9, 13), date(2026, 9, 30))
    assert runs == sorted(runs, key=lambda r: r.run_date)


def test_plan_follows_the_config_schedule(tmp_path: Path) -> None:
    # Move the weekly review to Tuesdays: the plan follows the config, not fixed weekdays.
    config = yaml.safe_load(builder.CONFIG_PATH.read_text(encoding="utf-8"))
    config["weekly_sales_review"]["schedule"]["day_of_week"] = "tue"
    path = tmp_path / "reports.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    runs = backfill.plan_runs(settings_for(config_path=path), START, END)
    weekly = [r.run_date for r in runs if r.report == "weekly_sales_review"]
    assert weekly == [date(2026, 9, 15), date(2026, 9, 22), date(2026, 9, 29)]


def test_to_before_from_is_refused() -> None:
    with pytest.raises(backfill.ConfigError):
        backfill.plan_runs(settings_for(), END, START)


# ---------------------------------------------------------------- forced dry run


def test_never_sends_or_audits_even_in_send_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("the backfill must never send or audit")

    # main() calls load_dotenv(); never let the real .env (LLM keys) into the test process.
    monkeypatch.setattr(backfill, "load_dotenv", lambda: None)
    monkeypatch.setenv("REPORT_MODE", "send")
    monkeypatch.setenv("DATA_END_DATE", DATA_END.isoformat())
    monkeypatch.setenv("TIMEZONE", "Europe/London")
    monkeypatch.setattr(emailer, "send_report", forbidden)
    monkeypatch.setattr(scheduler, "deliver_once", forbidden)
    monkeypatch.setattr(scheduler, "notify_failure", forbidden)
    monkeypatch.setattr(audit, "record_run", forbidden)
    modes: list[str] = []

    def recording_build(name: str, as_of: date, settings: scheduler.Settings) -> builder.Report:
        modes.append(settings.mode)
        return fake_build(name, as_of, settings)

    monkeypatch.setattr(scheduler, "build_once", recording_build)
    monkeypatch.setattr(backfill, "BACKFILL_ROOT", tmp_path)
    code = backfill.main(["--from", "2026-09-28", "--to", "2026-10-01", "--no-summary"])
    assert code == 0
    assert modes and set(modes) == {"dry_run"}
    assert len(read_manifest(tmp_path)) == len(modes)


# ---------------------------------------------------------------- manifest and index


def test_manifest_and_index_record_every_run_and_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def flaky_build(name: str, as_of: date, settings: scheduler.Settings) -> builder.Report:
        if name == "weekly_sales_review":
            raise ValueError("planning database is locked")
        return fake_build(name, as_of, settings)

    monkeypatch.setattr(scheduler, "build_once", flaky_build)
    rows = backfill.backfill(settings_for(), START, END, tmp_path)
    manifest = read_manifest(tmp_path)
    assert len(manifest) == len(rows) == 18
    assert list(manifest[0]) == backfill.MANIFEST_COLUMNS
    failed = [row for row in manifest if row["status"] == "failed"]
    assert [row["as_of"] for row in failed] == ["2026-09-13", "2026-09-20", "2026-09-27"]
    assert all("locked" in row["error"] and row["output_folder"] == "" for row in failed)
    monthly = next(row for row in manifest if row["report"] == "monthly_business_review")
    assert monthly["output_folder"] == "2026-10-01/monthly_business_review_2026-09-30"
    assert monthly["period"] == "September 2026" and monthly["summary_status"] == "ok"
    assert (tmp_path / monthly["output_folder"] / builder.HTML_FILENAME).exists()
    index = (tmp_path / backfill.INDEX_NAME).read_text(encoding="utf-8")
    assert index.count(f'/{builder.HTML_FILENAME}"') == 15  # every successful report
    assert index.count('class="failed"') == 3


# ---------------------------------------------------------------- real build


@pytest.mark.slow
def test_real_backfill_of_one_weekly_and_two_daily_reports(
    built: dict, demand_runs: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(emailer, "send_report", pytest.fail)
    settings = settings_for(mode="send", db_path=built["db_path"], summary=False)
    rows = backfill.backfill(settings, date(2026, 9, 28), date(2026, 9, 29), tmp_path)
    assert [(r["report"], r["status"]) for r in rows] == [
        ("daily_sales_snapshot", "ok"),
        ("weekly_sales_review", "ok"),
        ("daily_sales_snapshot", "ok"),
    ]
    weekly = tmp_path / rows[1]["output_folder"]
    assert (weekly / builder.HTML_FILENAME).exists()
    assert (weekly / "weekly_sales_review_2026-09-27.xlsx").exists()
    assert rows[1]["summary_status"] == "skipped"
