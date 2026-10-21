"""Scheduler run tests: modes, retry, failure notice and audit rows (plan sections 8 and 13).

SMTP is the FakeSMTP from conftest.py, so no real email is sent. Runs use "today"
2026-10-21 and DATA_END_DATE 2026-09-30, so the weekly report covers 21 to 27 Sep 2026.
"""

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
import yaml
from apscheduler.triggers.interval import IntervalTrigger

from src.reports import audit, builder, scheduler
from src.reports.delivery_config import ConfigError
from tests.conftest import OWNER, FakeSMTP

TODAY = date(2026, 10, 21)
DATA_END = date(2026, 9, 30)
RETRY_DELAY = 0.25  # seconds; recorded, never actually slept


@pytest.fixture
def sleeps() -> list[float]:
    """Delays the run asked to sleep for."""
    return []


@pytest.fixture
def settings(built: dict, tmp_path: Path, sleeps: list[float]) -> scheduler.Settings:
    """send-mode settings writing to temporary folders."""
    return scheduler.Settings(
        mode="send",
        timezone="UTC",
        data_end=DATA_END,
        retry_delay_seconds=RETRY_DELAY,
        db_path=built["db_path"],
        output_root=tmp_path / "reports",
        audit_path=tmp_path / "audit.duckdb",
        sleep=sleeps.append,
    )


def sent_subjects(smtp: type[FakeSMTP]) -> list[str]:
    """Subjects of every message the fake SMTP received."""
    return [msg["Subject"] for server in smtp.instances for msg, _, _ in server.sent]


def failing_build(fail_times: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make build_report raise for its first fail_times calls, then work normally."""
    real = builder.build_report
    calls = {"n": 0}

    def build(*args: object, **kwargs: object) -> builder.Report:
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise OSError("disk hiccup")
        return real(*args, **kwargs)

    monkeypatch.setattr(builder, "build_report", build)


def test_dry_run_sends_nothing(
    smtp: type[FakeSMTP], settings: scheduler.Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.mode = "dry_run"
    monkeypatch.delenv("GMAIL_APP_PASSWORD")  # dry run must not need it
    assert scheduler.run_report("weekly_sales_review", settings, "run_now", today=TODAY)
    assert smtp.instances == []
    assert (settings.output_root / "weekly_sales_review_2026-09-27" / "report.html").exists()
    row = audit.read_runs(settings.audit_path).iloc[0]
    assert (row["mode"], row["status"], row["triggered_by"]) == ("dry_run", "success", "run_now")


def test_send_success_writes_audit_row(smtp: type[FakeSMTP], settings: scheduler.Settings) -> None:
    assert scheduler.run_report("weekly_sales_review", settings, "schedule", today=TODAY)
    assert sent_subjects(smtp) == ["Weekly sales review: Week 39, 21 Sep to 27 Sep 2026"]
    runs = audit.read_runs(settings.audit_path)
    assert len(runs) == 1
    row = runs.iloc[0]
    assert row["report"] == "weekly_sales_review"
    assert row["status"] == "success" and row["attempt"] == 1
    assert pd_date(row["as_of"]) == date(2026, 9, 27)
    assert pd_date(row["period_start"]) == date(2026, 9, 21)
    assert pd_date(row["period_end"]) == date(2026, 9, 27)
    assert row["recipients"] == OWNER
    assert pd.isna(row["error"])
    assert row["duration_seconds"] >= 0
    assert row["finished_at_utc"] >= row["started_at_utc"]


@pytest.mark.parametrize(
    ("name", "as_of"),
    [
        ("daily_sales_snapshot", date(2026, 9, 30)),
        ("monthly_business_review", date(2026, 9, 30)),
    ],
)
def test_data_clock_picks_period(
    smtp: type[FakeSMTP], settings: scheduler.Settings, name: str, as_of: date
) -> None:
    settings.mode = "dry_run"
    assert scheduler.run_report(name, settings, "schedule", today=TODAY)
    assert pd_date(audit.read_runs(settings.audit_path).iloc[0]["as_of"]) == as_of


def test_retry_then_success(
    smtp: type[FakeSMTP],
    settings: scheduler.Settings,
    sleeps: list[float],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failing_build(1, monkeypatch)
    assert scheduler.run_report("weekly_sales_review", settings, "schedule", today=TODAY)
    assert sleeps == [RETRY_DELAY]
    runs = audit.read_runs(settings.audit_path)
    assert list(runs["status"]) == ["failed", "success"]
    assert list(runs["attempt"]) == [1, 2]
    assert runs["run_id"].nunique() == 1
    assert "OSError: disk hiccup" in runs.iloc[0]["error"]
    assert not any(s.startswith("FAILED") for s in sent_subjects(smtp))


def test_two_failures_send_failure_notice(
    smtp: type[FakeSMTP],
    settings: scheduler.Settings,
    sleeps: list[float],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failing_build(2, monkeypatch)
    assert not scheduler.run_report("weekly_sales_review", settings, "schedule", today=TODAY)
    assert sleeps == [RETRY_DELAY]
    assert list(audit.read_runs(settings.audit_path)["status"]) == ["failed", "failed"]
    assert sent_subjects(smtp) == ["FAILED: weekly_sales_review (2026-09-27)"]
    notice, _, to = smtp.instances[0].sent[0]
    assert to == [OWNER]
    assert "disk hiccup" in notice.get_body(("plain",)).get_content()


def test_disallowed_recipient_is_not_retried(
    smtp: type[FakeSMTP],
    settings: scheduler.Settings,
    sleeps: list[float],
    tmp_path: Path,
) -> None:
    config = builder.read_config_file()
    config["weekly_sales_review"]["recipients"].append("stranger@example.com")
    settings.config_path = tmp_path / "reports.yaml"
    settings.config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    assert not scheduler.run_report("weekly_sales_review", settings, "schedule", today=TODAY)
    assert sleeps == []  # config errors are not retried
    runs = audit.read_runs(settings.audit_path)
    assert len(runs) == 1 and "RecipientNotAllowedError" in runs.iloc[0]["error"]
    # Only the failure notice went out, and only to the owner.
    assert sent_subjects(smtp) == ["FAILED: weekly_sales_review (2026-09-27)"]


def test_missing_password_is_not_retried_and_logged(
    smtp: type[FakeSMTP],
    settings: scheduler.Settings,
    sleeps: list[float],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("GMAIL_APP_PASSWORD")
    assert not scheduler.run_report("weekly_sales_review", settings, "schedule", today=TODAY)
    assert sleeps == []
    assert smtp.instances == []  # neither the report nor the notice could be sent
    assert "GMAIL_APP_PASSWORD not set" in caplog.text
    assert "Could not send the failure notice" in caplog.text


def test_unknown_report_is_rejected(settings: scheduler.Settings) -> None:
    with pytest.raises(ConfigError, match="Unknown report"):
        scheduler.run_report("no_such_report", settings, "run_now", today=TODAY)


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"REPORT_MODE": "email"}, "REPORT_MODE must be one of"),
        ({"REPORT_RETRY_DELAY_SECONDS": "five"}, "REPORT_RETRY_DELAY_SECONDS"),
        ({"DATA_END_DATE": "Sept 30"}, "DATA_END_DATE"),
    ],
)
def test_bad_settings_give_clear_errors(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], message: str
) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ConfigError, match=message):
        scheduler.settings_from_env()


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("REPORT_MODE", "REPORT_RETRY_DELAY_SECONDS", "DATA_END_DATE", "TIMEZONE"):
        monkeypatch.delenv(key, raising=False)
    settings = scheduler.settings_from_env()
    assert settings.mode == "dry_run"
    assert settings.retry_delay_seconds == scheduler.DEFAULT_RETRY_DELAY_SECONDS
    assert settings.data_end is None


def pd_date(value: object) -> date:
    """A DuckDB DATE read back through pandas, as a date."""
    return value.date() if hasattr(value, "date") else value


# ---------------------------------------------------------------- command line


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep main() away from the real .env and log folder."""
    monkeypatch.setattr(scheduler, "load_dotenv", lambda: None)
    monkeypatch.setattr(scheduler, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setenv("TIMEZONE", "UTC")
    monkeypatch.setenv("REPORT_MODE", "dry_run")


def test_list_shows_all_jobs(cli_env: None, capsys: pytest.CaptureFixture) -> None:
    assert scheduler.main(["--list"]) == 0
    out = capsys.readouterr().out
    for name in ("daily_sales_snapshot", "weekly_sales_review", "monthly_business_review"):
        assert name in out
    assert "cron[day_of_week='mon', hour='8', minute='0']" in out


def test_cron_next_fire_times(settings: scheduler.Settings) -> None:
    now = datetime(2026, 10, 21, 12, 0, tzinfo=ZoneInfo("UTC"))  # a Wednesday
    lines = {
        line.split()[0]: line
        for line in scheduler.describe_jobs(scheduler.job_specs(settings), now)
    }
    assert "Thu 2026-10-22 07:00" in lines["daily_sales_snapshot"]
    assert "Mon 2026-10-26 08:00" in lines["weekly_sales_review"]
    assert "Sun 2026-11-01 08:00" in lines["monthly_business_review"]


def test_test_every_uses_interval_triggers(settings: scheduler.Settings) -> None:
    specs = scheduler.job_specs(settings, test_every_minutes=2)
    assert len(specs) == 3
    for spec in specs:
        assert isinstance(spec.trigger, IntervalTrigger)
        assert spec.trigger.interval.total_seconds() == 120
        assert spec.triggered_by == "test"
    jobs = scheduler.build_scheduler(settings, specs).get_jobs()
    assert sorted(job.id for job in jobs) == sorted(spec.name for spec in specs)


@pytest.mark.parametrize("args", [["--run-now", "weekly_sales_review"], ["--test-every", "2"], []])
def test_missing_password_is_one_clear_line(
    cli_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    args: list[str],
) -> None:
    monkeypatch.setenv("REPORT_MODE", "send")
    monkeypatch.setenv("REPORT_OWNER_EMAIL", OWNER)
    monkeypatch.setenv("GMAIL_ADDRESS", "sender@example.com")
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    assert scheduler.main(args) == 1
    err = capsys.readouterr().err
    assert err.startswith("Error: GMAIL_APP_PASSWORD not set in .env.")
    assert "Traceback" not in err


def test_run_now_unknown_report(cli_env: None, capsys: pytest.CaptureFixture) -> None:
    assert scheduler.main(["--run-now", "no_such_report"]) == 1
    assert "Unknown report 'no_such_report'" in capsys.readouterr().err
