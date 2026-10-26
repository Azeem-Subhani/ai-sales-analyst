"""The tool steps shown in a chat answer: live while it runs, then one collapsed line.

The chat page feeds agent.ask's progress events into Step objects and draws them with
the functions here. Everything is plain HTML and CSS (style.css), no JavaScript: a
step is a small timeline row (icon, label, duration); the active one animates (a ring
spins around a running tool, the sparkles shimmer) and finished ones keep their duration.
The collapsed view opens with <details>/<summary>; each run_sql step has its own closed
"View SQL query" toggle. The model, tokens and cost line sits inside the opened view only.

The UI calculates nothing: durations come from the agent's progress events and the
total from AgentAnswer.latency_seconds; this module only formats them. Every dynamic
string (labels, SQL, tool results) is escaped before it goes into HTML.
"""

import json
import re
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
# Kinds of step that are not a tool call, with (running, done) labels. "thinking" is
# the first model call, "review" a model call after a tool result, "check" the answer
# number guard, "write" the answer being shown (the page's typewriter; it has no time).
THINKING, REVIEW, CHECK, WRITE, NOTE = "thinking", "review", "check", "write", "note"
KIND_LABELS = {
    THINKING: (THINKING_LABEL, "Thought"),
    REVIEW: ("Reviewing results", "Reviewed results"),
    CHECK: ("Checking numbers", "Checked numbers"),
    WRITE: ("Writing answer", "Wrote answer"),
}
# Step state -> Lucide icon. A running tool shows the wrench inside a spinning ring.
STATE_ICONS = {
    "thinking": "sparkles",
    "review": "sparkles",
    "check": "shield-check",
    "write": "pencil-line",
    "running": "wrench",
    "ok": "circle-check",
    "fail": "circle-x",
    "note": "info",
}
MS_PER_SECOND = 1000
SHORT_SECONDS = 10  # below this, durations show one decimal ("7.3 s")
SECONDS_PER_MINUTE = 60


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
    """One tool call, a model call, the number check, or a note (a fallback model, a retry)."""

    tool: str | None = None  # None for everything but a tool call
    note: str = ""  # the text of a note
    state: str = "running"  # running | ok | fail
    seconds: float | None = None
    arguments: str | None = None  # the tool's JSON arguments
    result: dict | None = None
    kind: str = NOTE  # for a step without a tool: THINKING | REVIEW | CHECK | WRITE | NOTE

    def label(self) -> str:
        """Present tense while running, past tense when done; notes as they are."""
        if self.tool is None and self.kind == NOTE:
            return self.note
        running, done = readable_labels(self.tool) if self.tool else KIND_LABELS[self.kind]
        if self.state == "running":
            return running
        return done if self.state == "ok" else f"{done} (failed)"


def duration_text(seconds: float | None) -> str:
    """A duration people can trust: '<1 ms', '84 ms', '7.3 s', '42 s', '2 min 5 s'; '' if unknown.

    Rounded once, here. Fast steps show milliseconds, never "0.0 s".
    """
    if seconds is None:
        return ""
    millis = round(seconds * MS_PER_SECOND)
    if seconds < 1 / MS_PER_SECOND:
        return "<1 ms"
    if millis < MS_PER_SECOND:
        return f"{millis} ms"
    if seconds < SHORT_SECONDS:
        return f"{seconds:.1f} s"
    whole = round(seconds)
    if whole < SECONDS_PER_MINUTE:
        return f"{whole} s"
    minutes, rest = divmod(whole, SECONDS_PER_MINUTE)
    return f"{minutes} min {rest} s" if rest else f"{minutes} min"


def state_of(step: Step, active: bool) -> str:
    """What the step's icon and colour show: its kind while active, else its outcome."""
    if step.tool is None and step.kind == NOTE:
        return "note"
    running = active or step.state == "running"
    if running:
        return "running" if step.tool is not None else step.kind
    return "fail" if step.state == "fail" else "ok"


def row_html(step: Step, active: bool = False) -> str:
    """One step line: an icon (in a fixed box, so it centres on the label), the label, the time."""
    state = state_of(step, active)
    active = active or step.state == "running"
    kind = "is-active" if active else f"is-{state}"
    return (
        f'<div class="step-row {kind} step-{state}">'
        f'<span class="step-mark">{icons.icon(STATE_ICONS[state], "step-icon")}</span>'
        f'<span class="step-label">{escape(step.label())}</span>'
        f'<span class="step-time">{escape(duration_text(step.seconds))}</span></div>'
    )


def live_html(steps: list[Step]) -> str:
    """The steps while the answer is being worked out: a compact timeline.

    Finished steps keep a check (a cross if they failed) and their duration; the one
    running step animates. The page adds a Thinking step before the model starts.
    """
    rows = [row_html(s, active=s.state == "running") for s in steps]
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
    return f"{head} · {duration_text(total_seconds)}"


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


# The one script of the chat steps. st.html strips inline event handlers, so the copy
# buttons are served by a single click listener on the page (installed once, from the
# chat page with unsafe_allow_javascript). It copies the textContent of the <pre> next to the
# button, so the clipboard holds exactly what is shown. The script is a fixed string:
# no dynamic text goes into it.
COPY_SCRIPT = """<script>
(function () {
  if (window.__copyButtonsBound) return;
  window.__copyButtonsBound = true;
  function fallbackCopy(text) {
    var area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (error) { ok = false; }
    document.body.removeChild(area);
    return ok;
  }
  function markCopied(button) {
    button.classList.add("copied");
    setTimeout(function () { button.classList.remove("copied"); }, 1500);
  }
  document.addEventListener("click", function (event) {
    var button = event.target.closest(".copy-btn");
    if (!button) return;
    var block = button.parentElement.querySelector("pre");
    var text = block ? block.textContent : "";
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(
        function () { markCopied(button); },
        function () { if (fallbackCopy(text)) markCopied(button); }
      );
    } else if (fallbackCopy(text)) {
      markCopied(button);
    }
  });
})();
</script>"""


BLANK_LINES = re.compile(r"\n{3,}")
SQL_BLOCK = re.compile(r"```sql\b.*?```", re.DOTALL | re.IGNORECASE)  # a fenced query


def hide_sql(text: str) -> str:
    """The answer without its ```sql blocks: the query is in the steps view, with a copy button.

    Display only. The stored answer (history, logs, the CLI) keeps the query.
    """
    return BLANK_LINES.sub("\n\n", SQL_BLOCK.sub("", text)).strip()


def copy_block(pre_html: str) -> str:
    """A code block with a copy button in its corner (the button is wired by COPY_SCRIPT)."""
    button = (
        '<button type="button" class="copy-btn" title="Copy" aria-label="Copy">'
        f"{icons.icon('copy', 'copy-icon')}{icons.icon('check', 'copied-icon')}</button>"
    )
    return f'<div class="copy-wrap">{pre_html}{button}</div>'


def data_html(result: dict | None) -> str:
    """A collapsed 'Data' view of one tool result: escaped, pretty-printed JSON."""
    text = json.dumps(preview(result or {}), indent=2, ensure_ascii=False, default=str)
    return (
        '<details class="step-data"><summary>Data</summary>'
        f"{copy_block(f'<pre class="step-json">{escape(text)}</pre>')}</details>"
    )


# Words coloured in a shown query (display only: the UI never runs or builds SQL).
SQL_KEYWORDS = (
    "select",
    "from",
    "where",
    "group",
    "by",
    "order",
    "having",
    "limit",
    "offset",
    "join",
    "left",
    "right",
    "inner",
    "outer",
    "full",
    "cross",
    "on",
    "as",
    "and",
    "or",
    "not",
    "in",
    "is",
    "null",
    "like",
    "between",
    "case",
    "when",
    "then",
    "else",
    "end",
    "distinct",
    "union",
    "all",
    "with",
    "over",
    "partition",
    "asc",
    "desc",
    "count",
    "sum",
    "avg",
    "min",
    "max",
    "cast",
    "date",
    "interval",
    "true",
    "false",
)
SQL_TOKEN = re.compile(
    r"(?P<str>'(?:[^']|'')*'?)|(?P<com>--[^\n]*)|(?P<num>\b\d+(?:\.\d+)?\b)"
    rf"|(?P<kw>\b(?:{'|'.join(SQL_KEYWORDS)})\b)",
    re.IGNORECASE,
)
SQL_CLASSES = {"str": "sql-str", "com": "sql-com", "num": "sql-num", "kw": "sql-kw"}


def highlight_sql(query: str) -> str:
    """The query as escaped HTML with keywords, strings, numbers and comments in spans.

    Every piece of the text is escaped on its own before a span is put around it, so
    a query can never add markup.
    """
    out, position = [], 0
    for match in SQL_TOKEN.finditer(query):
        out.append(escape(query[position : match.start()]))
        css = SQL_CLASSES[match.lastgroup]
        out.append(f'<span class="{css}">{escape(match.group())}</span>')
        position = match.end()
    out.append(escape(query[position:]))
    return "".join(out)


def sql_html(query: str) -> str:
    """The query behind a closed 'View SQL query' toggle."""
    return (
        '<details class="step-sql-toggle"><summary>View SQL query</summary>'
        f"{copy_block(f'<pre class="step-sql"><code>{highlight_sql(query)}</code></pre>')}"
        "</details>"
    )


def detail_html(step: Step) -> str:
    """One step in the expanded view: its line, the SQL toggle for run_sql, and its Data."""
    parts = [row_html(step)]
    if step.tool == "run_sql" and (query := sql_of(step.arguments)):
        parts.append(sql_html(query))
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
