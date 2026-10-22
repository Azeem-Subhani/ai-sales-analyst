"""The tool steps shown in a chat answer: live while it runs, then one collapsed line.

The chat page feeds agent.ask's progress events into Step objects and draws them with
the functions here. Everything is plain HTML and CSS (style.css): no spinners, no
JavaScript; the active step's label shimmers, and the collapsed view opens with
<details>/<summary>. The model, tokens and cost line sits inside the opened view only.

The UI calculates nothing: durations come from the agent's progress events and the
total from AgentAnswer.latency_seconds; this module only formats them. Every dynamic
string (labels, SQL, tool results) is escaped before it goes into HTML.
"""

import json
from dataclasses import dataclass
from html import escape
from typing import Any

from src.ui import icons

THINKING_LABEL = "Thinking"
DATA_PREVIEW_ROWS = 20  # rows per list in a step's Data view; the rest becomes "… N more rows"
# Tool name -> (label while running, label when done). The one place step labels live.
STEP_LABELS = {
    "get_kpis": ("Calculating KPIs", "Calculated KPIs"),
    "run_sql": ("Querying sales data", "Queried sales data"),
    "make_chart": ("Drawing a chart", "Drew a chart"),
    "get_forecast": ("Checking the forecast", "Checked the forecast"),
    "get_customer_alerts": ("Checking customer alerts", "Checked customer alerts"),
    "get_forecast_accuracy": ("Checking forecast accuracy", "Checked forecast accuracy"),
    "get_prebuild_recommendations": (
        "Checking pre-build recommendations",
        "Checked pre-build recommendations",
    ),
}
UNKNOWN_TOOL_LABELS = ("Using a tool", "Used a tool")
# A tool without its own labels: the first word of its name as a verb, the rest as
# words ("get_customer_list" -> "Getting customer list" / "Got customer list").
VERB_FORMS = {
    "get": ("Getting", "Got"),
    "run": ("Running", "Ran"),
    "make": ("Making", "Made"),
    "check": ("Checking", "Checked"),
    "list": ("Listing", "Listed"),
    "find": ("Finding", "Found"),
    "calculate": ("Calculating", "Calculated"),
}
# Step state -> Lucide icon: the model thinking, a tool running, done, failed, a note.
STATE_ICONS = {
    "thinking": "brain",
    "running": "wrench",
    "ok": "circle-check",
    "fail": "circle-x",
    "note": "info",
}


def readable_labels(tool: str) -> tuple[str, str]:
    """(running, done) labels for any tool name, never the raw name."""
    if tool in STEP_LABELS:
        return STEP_LABELS[tool]
    verb, *rest = tool.split("_")
    words = " ".join(rest)
    if verb in VERB_FORMS and words:
        running, done = VERB_FORMS[verb]
        return f"{running} {words}", f"{done} {words}"
    return UNKNOWN_TOOL_LABELS


@dataclass
class Step:
    """One tool call, or a note such as a fallback model or a number-check retry."""

    tool: str | None = None  # None for a note
    note: str = ""
    state: str = "running"  # running | ok | fail
    seconds: float | None = None
    arguments: str | None = None  # the tool's JSON arguments
    result: dict | None = None

    def label(self) -> str:
        """Present tense while running, past tense when done; notes as they are."""
        if self.tool is None:
            return self.note
        running, done = readable_labels(self.tool)
        if self.state == "running":
            return running
        return done if self.state == "ok" else f"{done} (failed)"


def seconds_text(seconds: float | None) -> str:
    """'7.3 s', or '' when unknown."""
    return "" if seconds is None else f"{seconds:.1f} s"


def state_of(step: Step, active: bool) -> str:
    """thinking | running | ok | fail | note: what the step's icon and colour show."""
    if step.tool is None:
        return "thinking" if active else "note"
    if active or step.state == "running":
        return "running"
    return "fail" if step.state == "fail" else "ok"


def row_html(step: Step, active: bool = False) -> str:
    """One step line: a state icon, the label (shimmering when active), the duration."""
    state = state_of(step, active)
    kind = "is-active" if active else f"is-{state}"
    time_text = seconds_text(step.seconds)
    return (
        f'<div class="step-row {kind} step-{state}">'
        f"{icons.icon(STATE_ICONS[state], 'step-icon')}"
        f'<span class="step-label">{escape(step.label())}</span>'
        f'<span class="step-time">{escape(time_text)}</span></div>'
    )


def live_html(steps: list[Step], thinking: bool) -> str:
    """The steps while the answer is being worked out.

    A running tool shows the wrench and its label shimmers. While the model itself is
    working (before the first tool, and between tools), a "Thinking" line with the
    brain shimmers instead. Finished tools keep a check (or a cross if they failed).
    """
    rows = [row_html(s, active=s.state == "running") for s in steps]
    if thinking and not any(s.state == "running" for s in steps):
        rows.append(row_html(Step(note=THINKING_LABEL), active=True))
    return f'<div class="steps steps-live">{"".join(rows)}</div>'


def tool_steps(steps: list[Step]) -> list[Step]:
    """Only the tool calls (notes are not counted as steps)."""
    return [s for s in steps if s.tool is not None]


def summary_text(steps: list[Step], total_seconds: float) -> str:
    """'Checked forecast accuracy · 7.3 s', '3 steps · 9.1 s' or 'No tools used · 2.0 s'."""
    tools = tool_steps(steps)
    if len(tools) == 1:
        head = tools[0].label()
    elif tools:
        head = f"{len(tools)} steps"
    else:
        head = "No tools used"
    return f"{head} · {seconds_text(total_seconds)}"


def sql_of(arguments: str | None) -> str | None:
    """The query of a run_sql call, from its JSON arguments."""
    try:
        query = json.loads(arguments or "{}").get("query")
    except (json.JSONDecodeError, AttributeError):
        return None
    return query if isinstance(query, str) else None


def preview(value: Any) -> Any:
    """The tool result with every list cut to DATA_PREVIEW_ROWS, plus a '… N more rows' line."""
    if isinstance(value, dict):
        return {key: preview(item) for key, item in value.items()}
    if isinstance(value, list):
        shown = [preview(item) for item in value[:DATA_PREVIEW_ROWS]]
        if len(value) > DATA_PREVIEW_ROWS:
            shown.append(f"… {len(value) - DATA_PREVIEW_ROWS} more rows")
        return shown
    return value


def data_html(result: dict | None) -> str:
    """A collapsed 'Data' view of one tool result: escaped, pretty-printed JSON."""
    text = json.dumps(preview(result or {}), indent=2, ensure_ascii=False, default=str)
    return (
        '<details class="step-data"><summary>Data</summary>'
        f'<pre class="step-json">{escape(text)}</pre></details>'
    )


def detail_html(step: Step) -> str:
    """One step in the expanded view: its line, the SQL for run_sql, and its Data."""
    parts = [row_html(step)]
    if step.tool == "run_sql" and (query := sql_of(step.arguments)):
        parts.append(f'<pre class="step-sql"><code>{escape(query)}</code></pre>')
    if step.tool is not None and step.state != "running":
        parts.append(data_html(step.result))
    return f'<div class="step-detail">{"".join(parts)}</div>'


def collapsed_html(steps: list[Step], total_seconds: float, meta: str = "") -> str:
    """The finished steps as one line that opens to show each step (no JavaScript).

    meta (model, tokens, cost, time) is shown at the end of the opened view only.
    """
    summary = escape(summary_text(steps, total_seconds))
    if not steps and not meta:
        return f'<div class="steps steps-plain">{summary}</div>'
    body = "".join(detail_html(s) for s in steps)
    if meta:
        body += f'<div class="chat-meta">{escape(meta)}</div>'
    # A failed tool turns the line's check into a cross (style.css: steps-failed).
    failed = " steps-failed" if any(step.state == "fail" for step in steps) else ""
    return (
        f'<details class="steps steps-done{failed}"><summary>{summary}</summary>'
        f'<div class="steps-body">{body}</div></details>'
    )
