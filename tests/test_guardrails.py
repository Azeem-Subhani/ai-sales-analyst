"""Guardrail tests that back up the Claude Code hooks.

These catch the same problems for changes made in Cursor or by hand:
only src/agent/llm.py may import openai, and .env must never be tracked by git.
"""

import re
import shutil
import subprocess
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
