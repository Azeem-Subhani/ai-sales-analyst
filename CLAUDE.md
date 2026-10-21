# CLAUDE.md

@AGENTS.md

## Claude Code specifics

Everything above comes from `AGENTS.md`. Only Claude-specific notes go below.

### Project skills (`.claude/skills/`)

Use the matching skill when the task fits:

- `add-report`: add a scheduled report.
- `add-agent-tool`: add a tool to the AI agent.
- `update-dummy-data`: change the data generator safely.
- `compare-models`: run the model comparison and log the decision.

### Hooks (`.claude/settings.json`, scripts in `.claude/hooks/`)

- **PreToolUse `guard_edits.py`** on Edit/Write/MultiEdit and Bash. It blocks:
  - an edit that adds `import openai` / `from openai` to any file other than
    `src/agent/llm.py`;
  - writing an OpenRouter key pattern (`sk-or-...`) into any file;
  - shell commands that read `.env` (`.env.example` is fine).
- **PostToolUse `format_python.py`** on Edit/Write/MultiEdit: runs
  `ruff format` and `ruff check --fix` on the edited `.py` file.

Hooks run with the project's `.venv` Python (`.venv/bin/python` on macOS,
`.venv/Scripts/python.exe` on Windows), falling back to `python` on `PATH`.
If ruff is missing, the format hook skips without failing.

Permissions in `settings.json` deny reading or editing `.env` and
`data/*.duckdb` with file tools, `git push --force` / `-f`, and `rm -rf`.
Personal overrides go in `.claude/settings.local.json` (git-ignored).
