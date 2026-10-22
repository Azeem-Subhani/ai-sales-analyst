"""Guardrail tests that back up the Claude Code hooks.

These catch the same problems for changes made in Cursor or by hand:
only src/agent/llm.py may import openai, and .env must never be tracked by git.

Week 6 adds the dashboard and backfill rules: src/ui has no SQL and opens no database
itself, approval statuses change only through approve.decide() (called only by the
approval CLI and the dashboard's Demand page), the agent and the chat page cannot
reach decide(), the backfill never sends, and demand-tool lists stay at 10 rows.
"""

import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALLOWED_OPENAI_FILE = PROJECT_ROOT / "src" / "agent" / "llm.py"
SKIPPED_DIRS = {".venv", ".git", "outputs", "data", "__pycache__"}
OPENAI_IMPORT = re.compile(r"^\s*(import\s+openai\b|from\s+openai\b)", re.MULTILINE)


def project_python_files() -> list[Path]:
    """Return all .py files in the project, skipping the venv and generated folders."""
    return [
        path
        for path in PROJECT_ROOT.rglob("*.py")
        if not SKIPPED_DIRS.intersection(path.relative_to(PROJECT_ROOT).parts)
    ]


def test_only_llm_module_imports_openai() -> None:
    """Design rule 3: every model call goes through src/agent/llm.py."""
    offenders = [
        str(path.relative_to(PROJECT_ROOT))
        for path in project_python_files()
        if path != ALLOWED_OPENAI_FILE and OPENAI_IMPORT.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"Only src/agent/llm.py may import openai. Found in: {offenders}"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_env_file_is_not_tracked_by_git() -> None:
    """The real .env holds secrets and must never be committed."""
    result = subprocess.run(
        ["git", "ls-files", "--", ".env"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "", ".env is tracked by git. Run: git rm --cached .env"


def test_env_file_is_git_ignored() -> None:
    """.gitignore must list .env so it cannot be added by accident."""
    ignore_lines = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in [line.strip() for line in ignore_lines]


# ---------------------------------------------------------------- Week 6

UI_DIR = PROJECT_ROOT / "src" / "ui"
APPROVE_FILE = PROJECT_ROOT / "src" / "demand" / "approve.py"
DEMAND_PAGE = UI_DIR / "app_pages" / "demand.py"
CHAT_PAGE = UI_DIR / "app_pages" / "chat.py"
BACKFILL_FILE = PROJECT_ROOT / "src" / "reports" / "backfill.py"
SQL_TEXT = re.compile(
    r"\bselect\b.+\bfrom\b|\binsert\s+into\b|\bupdate\s+\w+\s+set\b|\bdelete\s+from\b"
    r"|\bcreate\s+(table|view)\b|\bdrop\s+table\b|\balter\s+table\b",
    re.IGNORECASE | re.DOTALL,
)
STATUS_WRITES = ("UPDATE prebuild_recommendations", "INSERT INTO prebuild_decisions")


def ui_files() -> list[Path]:
    """Every Python file of the dashboard."""
    return sorted(UI_DIR.rglob("*.py"))


def names_used(path: Path) -> set[str]:
    """Every name and attribute name the code uses (comments and docstrings ignored)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, ast.alias):
            used.add(node.name.split(".")[-1])
            used.add(node.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            used.add(node.module)
    return used


def string_literals(path: Path) -> list[str]:
    """Every string constant in a file, docstrings included."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def test_ui_opens_no_database_and_imports_no_openai() -> None:
    """The dashboard reads through src/ helpers that open DuckDB read-only."""
    assert ui_files(), "src/ui is missing"
    for path in ui_files():
        used = names_used(path)
        assert "openai" not in used, path
        assert "duckdb" not in used and "connect" not in used, path  # no own connections
        assert "connect_write" not in used and "read_only" not in used, path


def test_ui_has_no_sql() -> None:
    """Every number on the dashboard comes from src/; the UI has no SQL of its own."""
    for path in ui_files():
        found = [text for text in string_literals(path) if SQL_TEXT.search(text)]
        assert found == [], f"SQL in {path}: {found}"


def test_status_changes_only_through_decide() -> None:
    """Only approve.py writes a pre-build status; only the CLI and Demand page call decide()."""
    for path in (PROJECT_ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if path != APPROVE_FILE:
            for statement in STATUS_WRITES:
                assert statement not in text, f"{statement} in {path}"
        if "decide" in names_used(path) and path not in (APPROVE_FILE, DEMAND_PAGE):
            pytest.fail(f"{path} calls decide(); only the approval CLI and the Demand page may")


def test_agent_and_chat_page_cannot_reach_decide() -> None:
    """Importing the agent, its tools and the chat page's modules never loads approve.py."""
    code = (
        "import sys; import src.agent.agent, src.agent.tools, src.agent.chat, src.ui.common; "
        "print('src.demand.approve' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=PROJECT_ROOT, capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"
    for path in [*(PROJECT_ROOT / "src" / "agent").rglob("*.py"), CHAT_PAGE]:
        used = names_used(path)
        assert "approve" not in used and "src.demand.approve" not in used, path
        assert "decide" not in used, path


def test_backfill_never_sends_or_audits() -> None:
    """The backfill is a forced dry run: no send_report, deliver_once or report_runs write."""
    used = names_used(BACKFILL_FILE)
    for forbidden in ("send_report", "deliver_once", "notify_failure", "run_report", "record_run"):
        assert forbidden not in used, forbidden
    assert "emailer" not in used and "audit" not in used


def capped_lists(value: object) -> list[dict]:
    """Every capped list ({"total_count", "more_count", "rows"}) inside a tool result."""
    if isinstance(value, dict):
        found = [value] if "more_count" in value else []
        return found + [item for v in value.values() for item in capped_lists(v)]
    if isinstance(value, list):
        return [item for v in value for item in capped_lists(v)]
    return []


def test_demand_tools_never_return_more_than_10_rows(built: dict, demand_runs: dict) -> None:
    """Every argument combination of the three demand tools stays within the cap."""
    from datetime import date

    from src.agent import tools

    session = tools.ToolSession(
        tools.ToolContext(db_path=built["db_path"], data_end=date(2026, 9, 30))
    )
    calls = [
        ("get_customer_alerts", {}),
        ("get_forecast_accuracy", {}),
        ("get_prebuild_recommendations", {}),
    ]
    calls += [
        ("get_customer_alerts", {"alert_type": t})
        for t in ("overdue", "declining", "new_high_value", "segment_change")
    ]
    calls += [("get_customer_alerts", {"severity": s}) for s in ("serious", "overdue", "warning")]
    calls += [("get_forecast_accuracy", {"category": "Apparel"})]
    calls += [("get_prebuild_recommendations", {"status": "pending"})]
    big = 0
    for name, arguments in calls:
        outcome = session.execute(name, json.dumps(arguments))
        assert outcome.success, (name, arguments, outcome.result)
        lists = capped_lists(outcome.result)
        assert lists, (name, arguments)
        for item in lists:
            assert len(item["rows"]) <= tools.DEMAND_LIST_CAP, (name, arguments)
            assert item["more_count"] == max(0, item["total_count"] - tools.DEMAND_LIST_CAP)
            big += item["total_count"] > tools.DEMAND_LIST_CAP
    assert big, "the fixture has no list longer than 10, so the cap was not exercised"
