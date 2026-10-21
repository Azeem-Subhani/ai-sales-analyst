"""PreToolUse hook: block edits and commands that break project guardrails.

Claude Code sends the pending tool call as JSON on stdin. Exiting with code 2
blocks the call and shows our stderr message to Claude. Exit 0 allows it.
Standard library only, so it starts fast on Windows and macOS.
"""

import json
import re
import sys
from pathlib import PurePosixPath

BLOCK_EXIT_CODE = 2
ALLOWED_OPENAI_FILE = "src/agent/llm.py"

# "import openai", "import openai as x", "from openai import OpenAI", etc.
OPENAI_IMPORT = re.compile(r"^\s*(import\s+openai\b|from\s+openai\b)", re.MULTILINE)
# OpenRouter keys look like "sk-or-v1-<hex>". "sk-or-..." in docs does not match.
API_KEY = re.compile(r"sk-or-[A-Za-z0-9_-]{8,}")
# ".env" as a whole file name; not ".env.example", ".venv" or "x.env".
ENV_FILE = r"(?<![\w.])\.env(?![\w.])"
# Shell commands that would print or copy the contents of .env. The match stops at
# a pipe, ;, & or line break, so text in a later heredoc line does not trigger it.
ENV_READ_COMMAND = re.compile(
    r"(\b(cat|less|more|head|tail|grep|rg|sed|awk|source|cp|mv|base64|xxd|"
    r"strings|vi|vim|nano|open|code|bat|type|Get-Content)\b[^|;&\n]*|<\s*)" + ENV_FILE
)


def relative_path(file_path: str, project_dir: str) -> str:
    """Return file_path relative to the project root, with forward slashes."""
    path = file_path.replace("\\", "/")
    root = project_dir.replace("\\", "/").rstrip("/") + "/"
    if root != "/" and path.lower().startswith(root.lower()):
        path = path[len(root) :]
    return str(PurePosixPath(path))


def new_text_of(tool_name: str, tool_input: dict) -> str:
    """Collect the text an Edit, Write or MultiEdit call would add."""
    if tool_name == "Write":
        return tool_input.get("content", "")
    if tool_name == "Edit":
        return tool_input.get("new_string", "")
    if tool_name == "MultiEdit":
        return "\n".join(e.get("new_string", "") for e in tool_input.get("edits", []))
    return ""


def check_file_edit(tool_name: str, tool_input: dict, project_dir: str) -> str | None:
    """Return a block reason for a file edit, or None if it is allowed."""
    text = new_text_of(tool_name, tool_input)
    rel_path = relative_path(tool_input.get("file_path", ""), project_dir)

    if API_KEY.search(text):
        return "Blocked: this edit contains an OpenRouter API key (sk-or-...). Keys belong in .env only."
    if OPENAI_IMPORT.search(text) and rel_path != ALLOWED_OPENAI_FILE:
        return (
            f"Blocked: only {ALLOWED_OPENAI_FILE} may import openai (design rule 3: one LLM client). "
            f"Call the functions in src/agent/llm.py from {rel_path} instead."
        )
    return None


def check_bash(command: str) -> str | None:
    """Return a block reason for a shell command, or None if it is allowed."""
    if ENV_READ_COMMAND.search(command):
        return "Blocked: do not read .env from the shell. Use .env.example to see which settings exist."
    if API_KEY.search(command):
        return "Blocked: this command contains an OpenRouter API key (sk-or-...)."
    return None


def main() -> int:
    """Read the hook payload from stdin and decide whether to block."""
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0  # Malformed input is Claude Code's problem, not a guardrail violation.

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}
    project_dir = payload.get("cwd", "")

    if tool_name == "Bash":
        reason = check_bash(tool_input.get("command", ""))
    else:
        reason = check_file_edit(tool_name, tool_input, project_dir)

    if reason:
        print(reason, file=sys.stderr)
        return BLOCK_EXIT_CODE
    return 0


if __name__ == "__main__":
    sys.exit(main())
