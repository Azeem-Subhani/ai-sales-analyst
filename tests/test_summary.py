"""LLM summary tests with a fake model: the guard, the retry, and reports that still send.

No test reaches OpenRouter: the fake replaces llm.complete, and the autouse fixture in
conftest.py blocks the host anyway.
"""

from datetime import date
from email import message_from_bytes, policy
from pathlib import Path

import duckdb
import pytest

from src.agent import llm
from src.reports import audit, builder, emailer, scheduler, summary
from tests.conftest import FakeSMTP

WEEKLY = "weekly_sales_review"
WEEKLY_AS_OF = date(2026, 9, 27)
TODAY = date(2026, 10, 21)
DATA_END = date(2026, 9, 30)
# Weekly 2026-09-27 figures (verified in Week 2; see docs/hand_checks).
GOOD = (
    "Net revenue reached 114,286.82 in week 39, up 2.5% on the previous week, "
    "from 661 orders. Gross margin was 43.1%. Electronics remained the top category."
)
INVENTED = "Net revenue reached 114,286.82 in week 39, from 700 orders."


class FakeLLM:
    """Returns the scripted texts in order (or raises), recording every call."""

    def __init__(self, *outcomes: str | llm.LLMResult | Exception) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[list[dict]] = []

    def __call__(self, messages: list[dict], **kwargs: object) -> llm.LLMResult:
        self.calls.append([dict(m) for m in messages])
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, llm.LLMResult):
            return outcome
        return llm.LLMResult(text=outcome, model="fake/model", cost_usd=0.001)


# The real cut-off seen in the Week 4 wrap-up: numbers fine, sentence unfinished.
CUT_OFF_TEXT = "Net revenue reached 114,286.82 in week 39, up 2.5% on the previous week, from"


def cut_off(text: str = CUT_OFF_TEXT) -> llm.LLMResult:
    """A reply that stopped at the max_tokens cap."""
    return llm.LLMResult(
        text=text,
        model="fake/model",
        cost_usd=0.001,
        output_tokens=summary.MAX_SUMMARY_TOKENS,
        reasoning_tokens=summary.MAX_SUMMARY_TOKENS - 20,
        finish_reason=llm.FINISH_LENGTH,
    )


@pytest.fixture(scope="module")
def weekly_numbers(built: dict, tmp_path_factory: pytest.TempPathFactory) -> dict:
    """The weekly report's numbers, built once without a summary."""
    out = tmp_path_factory.mktemp("summary_numbers")
    report = builder.build_report(
        WEEKLY, WEEKLY_AS_OF, db_path=built["db_path"], output_root=out, summary=False
    )
    return report.numbers


def build(built: dict, tmp_path: Path, **kwargs: object) -> builder.Report:
    """The weekly report built into tmp_path."""
    return builder.build_report(
        WEEKLY, WEEKLY_AS_OF, db_path=built["db_path"], output_root=tmp_path, **kwargs
    )


# ---------------------------------------------------------------- inputs and guard


def test_inputs_are_preformatted(weekly_numbers: dict) -> None:
    lines = summary.summary_inputs(weekly_numbers)
    assert any(line.startswith("- Net revenue: 114,286.82 (") for line in lines)
    assert any(line.startswith("- Orders: 661 (") for line in lines)
    assert any("vs previous week" in line for line in lines)
    assert lines[1].startswith("Period: Week 39, 21 Sep to 27 Sep 2026")


@pytest.mark.parametrize(
    "text",
    [
        "Net revenue was 114,286.82.",  # exact
        "Net revenue was 114286.82.",  # no commas
        "Net revenue was about 114,287.",  # rounded to fewer decimals
        "Net revenue was 114,286.8.",
        "Gross margin was 43.1% and 43%.",  # % sign, rounding
        "Week 39 ran from 21 Sep to 27 Sep 2026 (2026-09-21 to 2026-09-27).",  # dates
        "Three of the 8 categories grew; the top 3 products and 10 customers.",  # 1-10
        "Margin fell 0.5 pp.",
    ],
)
def test_guard_accepts_formatting_variants(weekly_numbers: dict, text: str) -> None:
    allowed = summary.allowed_numbers(summary.summary_inputs(weekly_numbers))
    assert summary.check_numbers(text, allowed) == []


@pytest.mark.parametrize(
    ("text", "bad"),
    [
        (INVENTED, ["700"]),
        ("Revenue grew 99.9% overall.", ["99.9"]),  # a calculated percentage
        ("Revenue was 114k.", ["114"]),  # an abbreviation is not a copy
        ("Net revenue was 114,286.83.", ["114,286.83"]),
        ("Orders hit 661 and then 9137.", ["9137"]),  # not in the data (12 now is: demand lists)
    ],
)
def test_guard_catches_invented_numbers(weekly_numbers: dict, text: str, bad: list[str]) -> None:
    allowed = summary.allowed_numbers(summary.summary_inputs(weekly_numbers))
    assert summary.check_numbers(text, allowed) == bad


def test_guard_ignores_digits_inside_words() -> None:
    assert summary.extract_numbers("B2B sales and AP-1150") == []
    assert summary.extract_numbers("down -25.4% over 21-27 Sep") == ["25.4", "21", "27"]


# ---------------------------------------------------------------- write_summary


def test_summary_ok(weekly_numbers: dict) -> None:
    fake = FakeLLM(GOOD)
    outcome = summary.write_summary(weekly_numbers, complete=fake)
    assert (outcome.status, outcome.text, outcome.attempts) == ("ok", GOOD, 1)
    prompt = fake.calls[0]
    for rule in ("Use only the numbers", "Do not do any arithmetic", "No greeting"):
        assert rule in prompt[0]["content"]
    assert "114,286.82" in prompt[1]["content"]


def test_retry_points_out_failing_numbers_then_succeeds(weekly_numbers: dict) -> None:
    fake = FakeLLM(INVENTED, GOOD)
    outcome = summary.write_summary(weekly_numbers, complete=fake)
    assert (outcome.status, outcome.text, outcome.attempts) == ("ok", GOOD, 2)
    retry_message = fake.calls[1][-1]["content"]
    assert "700" in retry_message and "not in the data" in retry_message
    assert outcome.cost_usd == pytest.approx(0.002)


def test_retry_then_drop(weekly_numbers: dict) -> None:
    fake = FakeLLM(INVENTED, INVENTED)
    outcome = summary.write_summary(weekly_numbers, complete=fake)
    assert (outcome.status, outcome.text, outcome.attempts) == ("guard_failed", None, 2)
    assert outcome.failed_numbers == ["700"]
    assert len(fake.calls) == 2


# The reply seen on 6 of 14 backfilled daily snapshots (Week 6).
REFUSAL = "I'm sorry, but I cannot assist with that request."
NO_NUMBERS = "Sales were broadly steady this week, with Electronics still the top category."


@pytest.mark.parametrize("text", [REFUSAL, NO_NUMBERS, "I can\u2019t help with that.", ""])
def test_a_reply_that_is_not_a_summary_is_retried_then_dropped(
    weekly_numbers: dict, text: str
) -> None:
    fake = FakeLLM(text, text)
    outcome = summary.write_summary(weekly_numbers, complete=fake)
    assert (outcome.status, outcome.text, outcome.attempts) == ("guard_failed", None, 2)
    assert outcome.failed_numbers == []  # no wrong number: the reply itself is the problem
    retry = fake.calls[1]
    assert retry[-2] == {"role": "assistant", "content": text}
    assert retry[-1]["content"] == summary.NOT_A_SUMMARY_PROMPT


def test_a_refusal_then_a_good_summary_is_ok(weekly_numbers: dict) -> None:
    outcome = summary.write_summary(weekly_numbers, complete=FakeLLM(REFUSAL, GOOD))
    assert (outcome.status, outcome.text, outcome.attempts) == ("ok", GOOD, 2)


def test_only_small_counts_is_not_enough() -> None:
    # 1 to 10 are allowed as counts, but the summary must use a number from the data.
    lines = ["Net revenue: 114,286.82 (+2.5% vs previous week)"]
    text = "Three categories grew and 5 products stood out."
    assert summary.check_numbers(text, summary.allowed_numbers(lines)) == []
    assert summary.not_a_summary(text, summary.input_numbers(lines)) == "no number from the data"
    assert summary.not_a_summary("Net revenue rose 2.5%.", summary.input_numbers(lines)) is None


def test_ordinary_wording_is_not_a_refusal(weekly_numbers: dict) -> None:
    lines = summary.summary_inputs(weekly_numbers)
    text = "Net revenue reached 114,286.82; we are unable to compare it with a forecast."
    assert summary.not_a_summary(text, summary.input_numbers(lines)) is None
    assert summary.not_a_summary(GOOD, summary.input_numbers(lines)) is None


def test_refused_report_still_sends_with_placeholder(
    built: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm, "complete", FakeLLM(REFUSAL, REFUSAL))
    report = build(built, tmp_path)
    assert report.summary_status == "guard_failed"
    assert "Summary unavailable for this run" in report.html
    assert "sorry" not in report.html.lower()


def test_cut_off_summary_is_retried_even_when_its_numbers_pass(weekly_numbers: dict) -> None:
    assert (
        summary.check_numbers(
            CUT_OFF_TEXT, summary.allowed_numbers(summary.summary_inputs(weekly_numbers))
        )
        == []
    )  # the guard alone would have let it through
    fake = FakeLLM(cut_off(), GOOD)
    outcome = summary.write_summary(weekly_numbers, complete=fake)
    assert (outcome.status, outcome.text, outcome.attempts) == ("ok", GOOD, 2)
    retry = fake.calls[1]
    assert retry[-2] == {"role": "assistant", "content": CUT_OFF_TEXT}
    assert "cut off" in retry[-1]["content"]


def test_cut_off_twice_is_dropped_as_truncated(weekly_numbers: dict) -> None:
    fake = FakeLLM(cut_off(), cut_off())
    outcome = summary.write_summary(weekly_numbers, complete=fake)
    assert (outcome.status, outcome.text, outcome.attempts) == ("truncated", None, 2)
    assert outcome.failed_numbers == []


def test_guard_failure_after_a_cut_off_is_guard_failed(weekly_numbers: dict) -> None:
    outcome = summary.write_summary(weekly_numbers, complete=FakeLLM(cut_off(), INVENTED))
    assert (outcome.status, outcome.failed_numbers) == ("guard_failed", ["700"])


def test_cut_off_report_still_sends_with_placeholder(
    built: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm, "complete", FakeLLM(cut_off(), cut_off()))
    report = build(built, tmp_path)
    assert report.summary_status == "truncated"
    assert builder.SUMMARY_PLACEHOLDER in report.html
    assert CUT_OFF_TEXT not in report.html


def test_summary_token_cap_leaves_room_for_reasoning() -> None:
    # 400 cut off a real monthly summary; the cap must cover hidden reasoning too.
    assert summary.MAX_SUMMARY_TOKENS >= 1000


@pytest.mark.parametrize(
    "error",
    [
        llm.LLMUnavailableError("both down"),
        llm.LLMAuthError("bad key"),
        llm.LLMConfigError("OPENROUTER_API_KEY not set in .env."),
    ],
)
def test_llm_problems_give_llm_unavailable(weekly_numbers: dict, error: Exception) -> None:
    outcome = summary.write_summary(weekly_numbers, complete=FakeLLM(error))
    assert (outcome.status, outcome.text) == ("llm_unavailable", None)


# ---------------------------------------------------------------- in the report


def test_summary_inserted_in_html_and_plain_text(
    built: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm, "complete", FakeLLM(GOOD))
    report = build(built, tmp_path)
    assert report.summary_status == "ok"
    assert report.numbers["summary"] == GOOD
    assert GOOD in report.html
    assert builder.SUMMARY_PLACEHOLDER not in report.html
    text = emailer.plain_text(report)
    assert GOOD in text
    assert text.index(GOOD) < text.index("Net revenue: 114,286.82")


def test_dropped_summary_shows_placeholder(
    built: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm, "complete", FakeLLM(INVENTED, INVENTED))
    report = build(built, tmp_path)
    assert report.summary_status == "guard_failed"
    assert "Summary unavailable for this run" in report.html
    assert "Summary unavailable for this run" in emailer.plain_text(report)
    assert "700 orders" not in report.html


def test_no_summary_makes_no_llm_call(
    built: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeLLM()
    monkeypatch.setattr(llm, "complete", fake)
    report = build(built, tmp_path, summary=False)
    assert report.summary_status == "skipped"
    assert fake.calls == []
    assert builder.SUMMARY_PLACEHOLDER in report.html


def test_no_summary_cli_flags() -> None:
    assert builder.parse_args(["--all", "--as-of", "2026-09-30", "--no-summary"]).no_summary
    assert scheduler.parse_args(["--run-now", WEEKLY, "--no-summary"]).no_summary
    assert not scheduler.parse_args(["--run-now", WEEKLY]).no_summary


# ---------------------------------------------------------------- scheduler and audit


@pytest.fixture
def send_settings(built: dict, tmp_path: Path) -> scheduler.Settings:
    """send-mode settings writing to temporary folders, no retry delay."""
    return scheduler.Settings(
        mode="send",
        timezone="UTC",
        data_end=DATA_END,
        retry_delay_seconds=0,
        db_path=built["db_path"],
        output_root=tmp_path / "reports",
        audit_path=tmp_path / "audit.duckdb",
        sleep=lambda _: None,
    )


def sent_text(smtp: type[FakeSMTP]) -> str:
    """The plain-text part of the single message sent."""
    msg, _, _ = smtp.instances[0].sent[0]
    parsed = message_from_bytes(msg.as_bytes(), policy=policy.default)
    return parsed.get_body(preferencelist=("plain",)).get_content()


def test_llm_down_report_still_sends(
    smtp: type[FakeSMTP], send_settings: scheduler.Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm, "complete", FakeLLM(llm.LLMUnavailableError("down")))
    assert scheduler.run_report(WEEKLY, send_settings, "run_now", today=TODAY)
    assert "Summary unavailable for this run" in sent_text(smtp)
    row = audit.read_runs(send_settings.audit_path).iloc[0]
    assert (row["status"], row["summary_status"]) == ("success", "llm_unavailable")


def test_summary_status_logged_ok(
    smtp: type[FakeSMTP], send_settings: scheduler.Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm, "complete", FakeLLM(GOOD))
    assert scheduler.run_report(WEEKLY, send_settings, "run_now", today=TODAY)
    assert GOOD in sent_text(smtp)
    row = audit.read_runs(send_settings.audit_path).iloc[0]
    assert row["summary_status"] == "ok"


def test_summary_status_logged_skipped(
    smtp: type[FakeSMTP], send_settings: scheduler.Settings
) -> None:
    send_settings.summary = False
    assert scheduler.run_report(WEEKLY, send_settings, "run_now", today=TODAY)
    assert audit.read_runs(send_settings.audit_path).iloc[0]["summary_status"] == "skipped"


def test_old_audit_table_is_migrated(tmp_path: Path) -> None:
    """A Week 3 audit.duckdb (no summary_status column) keeps working."""
    path = tmp_path / "audit.duckdb"
    week3_ddl = audit.CREATE_REPORT_RUNS.replace(
        ",\n    summary_status    VARCHAR             -- ok | guard_failed | llm_unavailable"
        " | skipped (Week 4)",
        "",
    )
    assert "summary_status" not in week3_ddl
    with duckdb.connect(str(path)) as con:
        con.execute(week3_ddl)
    record = audit.RunRecord(
        run_id="r1",
        report=WEEKLY,
        attempt=1,
        triggered_by="test",
        mode="dry_run",
        as_of=WEEKLY_AS_OF,
        period_start=None,
        period_end=None,
        recipients="",
        status="success",
        error=None,
        duration_seconds=0.1,
        started_at_utc=builder.pd.Timestamp("2026-10-21 08:00").to_pydatetime(),
        finished_at_utc=builder.pd.Timestamp("2026-10-21 08:01").to_pydatetime(),
        summary_status="ok",
    )
    audit.record_run(record, path)
    assert audit.read_runs(path).iloc[0]["summary_status"] == "ok"
