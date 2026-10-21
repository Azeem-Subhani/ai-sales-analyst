"""PostToolUse hook: run ruff format and ruff check --fix on an edited .py file.

Claude Code sends the finished tool call as JSON on stdin. If ruff leaves lint
errors it cannot fix, we exit 2 so Claude sees them and can fix them.
If ruff is not installed, the hook skips quietly instead of failing.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

LINT_ERRORS_EXIT_CODE = 2
RUFF_TIMEOUT_SECONDS = 8


def ruff_command() -> list[str] | None:
    """Find ruff: first next to this Python (the venv), then on PATH."""
    venv_bin = Path(sys.executable).parent
    for name in ("ruff", "ruff.exe"):
        candidate = venv_bin / name
        if candidate.exists():
            return [str(candidate)]
    on_path = shutil.which("ruff")
    return [on_path] if on_path else None


def main() -> int:
    """Format and lint the edited file if it is a Python file."""
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    file_path = (payload.get("tool_input") or {}).get("file_path", "")
    if not file_path.endswith(".py") or not Path(file_path).is_file():
        return 0

    ruff = ruff_command()
    if ruff is None:
        return 0  # Venv not set up or ruff not installed; skip rather than block work.

    # check=False: a non-zero ruff exit is expected when lint issues remain; we handle it below.
    run = {"capture_output": True, "text": True, "timeout": RUFF_TIMEOUT_SECONDS}
    subprocess.run([*ruff, "format", file_path], check=False, **run)
    check = subprocess.run([*ruff, "check", "--fix", file_path], check=False, **run)

    if check.returncode != 0:
        print(
            f"ruff found issues it could not fix in {file_path}:\n{check.stdout}", file=sys.stderr
        )
        return LINT_ERRORS_EXIT_CODE
    return 0


if __name__ == "__main__":
    sys.exit(main())
