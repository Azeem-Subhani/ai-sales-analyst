"""The agent_log tables in data/audit.duckdb (decisions Week 1 and Week 4).

agent_log has one row per question; agent_tool_calls has one row per tool call,
linked by question_id. Like report_runs, they live in audit.duckdb, never in
sales.duckdb, and the agent's own database connection never sees them. Timestamps are
UTC. Inserts name their columns, and new columns are added to older files by
MIGRATIONS, so an existing audit.duckdb keeps working.
"""

import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

from src.reports.audit import AUDIT_DB_PATH

CREATE_AGENT_LOG = """
CREATE TABLE IF NOT EXISTS agent_log (
    question_id       VARCHAR NOT NULL,
    asked_at_utc      TIMESTAMP NOT NULL,
    question          VARCHAR NOT NULL,
    model             VARCHAR,            -- model that gave the last answer
    fallback_used     BOOLEAN NOT NULL,
    tool_call_count   INTEGER NOT NULL,
    input_tokens      INTEGER NOT NULL,
    output_tokens     INTEGER NOT NULL,
    total_tokens      INTEGER NOT NULL,
    cost_usd          DOUBLE,             -- NULL when OpenRouter reported no cost
    latency_seconds   DOUBLE NOT NULL,
    stopped_early     VARCHAR,            -- NULL | tool_limit | time_limit
    final_answer      VARCHAR,
    error             VARCHAR,
    answer_guard      VARCHAR,            -- ok | retried_ok | withheld | not_run (wrap-up)
    answer_guard_failed VARCHAR           -- numbers no tool returned, comma-separated
)
"""
# Week 4 wrap-up: the answer number guard. NULL for questions logged before it.
MIGRATIONS = [
    "ALTER TABLE agent_log ADD COLUMN IF NOT EXISTS answer_guard VARCHAR",
    "ALTER TABLE agent_log ADD COLUMN IF NOT EXISTS answer_guard_failed VARCHAR",
]
CREATE_AGENT_TOOL_CALLS = """
CREATE TABLE IF NOT EXISTS agent_tool_calls (
    question_id       VARCHAR NOT NULL,
    call_index        INTEGER NOT NULL,   -- 1, 2, ... within the question
    tool_name         VARCHAR NOT NULL,
    arguments         VARCHAR,            -- raw JSON from the model
    success           BOOLEAN NOT NULL,
    row_count         INTEGER,
    error             VARCHAR,
    duration_seconds  DOUBLE NOT NULL,
    called_at_utc     TIMESTAMP NOT NULL
)
"""
INSERT_AGENT_LOG = """
INSERT INTO agent_log (
    question_id, asked_at_utc, question, model, fallback_used, tool_call_count,
    input_tokens, output_tokens, total_tokens, cost_usd, latency_seconds,
    stopped_early, final_answer, error, answer_guard, answer_guard_failed
) VALUES (
    $question_id, $asked_at_utc, $question, $model, $fallback_used, $tool_call_count,
    $input_tokens, $output_tokens, $total_tokens, $cost_usd, $latency_seconds,
    $stopped_early, $final_answer, $error, $answer_guard, $answer_guard_failed
)
"""
INSERT_TOOL_CALL = """
INSERT INTO agent_tool_calls (
    question_id, call_index, tool_name, arguments, success, row_count, error,
    duration_seconds, called_at_utc
) VALUES (
    $question_id, $call_index, $tool_name, $arguments, $success, $row_count, $error,
    $duration_seconds, $called_at_utc
)
"""
SELECT_QUESTIONS = "SELECT * FROM agent_log ORDER BY asked_at_utc"
SELECT_TOOL_CALLS = "SELECT * FROM agent_tool_calls ORDER BY called_at_utc, call_index"

_write_lock = threading.Lock()  # Streamlit (Week 6) may answer in threads


@dataclass
class QuestionRecord:
    """One question the agent answered (or failed to)."""

    question_id: str
    asked_at_utc: datetime
    question: str
    model: str | None
    fallback_used: bool
    tool_call_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float | None
    latency_seconds: float
    stopped_early: str | None
    final_answer: str | None
    error: str | None
    answer_guard: str | None = None
    answer_guard_failed: str | None = None


@dataclass
class ToolCallRecord:
    """One tool call made while answering a question."""

    question_id: str
    call_index: int
    tool_name: str
    arguments: str | None
    success: bool
    row_count: int | None
    error: str | None
    duration_seconds: float
    called_at_utc: datetime


def record_question(
    question: QuestionRecord,
    tool_calls: list[ToolCallRecord],
    db_path: Path = AUDIT_DB_PATH,
) -> None:
    """Write one question and its tool calls, creating the tables on first use."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock, duckdb.connect(str(db_path)) as con:
        con.execute(CREATE_AGENT_LOG)
        for migration in MIGRATIONS:
            con.execute(migration)
        con.execute(CREATE_AGENT_TOOL_CALLS)
        con.execute(INSERT_AGENT_LOG, asdict(question))
        for call in tool_calls:
            con.execute(INSERT_TOOL_CALL, asdict(call))


def read_questions(db_path: Path = AUDIT_DB_PATH) -> pd.DataFrame:
    """All rows of agent_log, oldest first (read-only)."""
    with duckdb.connect(str(db_path), read_only=True) as con:
        return con.execute(SELECT_QUESTIONS).df()


def read_tool_calls(db_path: Path = AUDIT_DB_PATH) -> pd.DataFrame:
    """All rows of agent_tool_calls, oldest first (read-only)."""
    with duckdb.connect(str(db_path), read_only=True) as con:
        return con.execute(SELECT_TOOL_CALLS).df()
