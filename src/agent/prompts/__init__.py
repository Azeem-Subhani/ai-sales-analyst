"""The agent's system prompt, built from files and the database (plan section 10).

system_prompt.md is the template. The parts that could drift are filled in at run
time instead of being typed by hand:
- table schemas and useful column values, read from sales.duckdb (read-only);
- KPI definitions, copied from docs/decisions.md (the agreed source);
- "today": DATA_END_DATE when set (the dummy data ends 2026-09-30), else the real date;
- the 5 example question/SQL pairs in examples.yaml, which tests run against the data.
"""

from datetime import date
from pathlib import Path

import yaml

from src.agent import sql_guard
from src.analytics import kpis
from src.reports import data_clock

PROMPT_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = PROMPT_DIR / "system_prompt.md"
EXAMPLES_PATH = PROMPT_DIR / "examples.yaml"
DECISIONS_PATH = PROMPT_DIR.parents[2] / "docs" / "decisions.md"
KPI_SECTION_START = "## Week 2: KPI definitions"
KPI_SECTION_END = "\n## "  # the next top-level section

TABLE_ORDER = ("customers", "products", "orders", "order_items", "inventory", "forecasts")
# Low-cardinality text columns whose values the model needs to write correct filters.
VALUE_COLUMNS = (
    ("orders", "status"),
    ("orders", "channel"),
    ("customers", "customer_type"),
    ("customers", "country"),
    ("products", "category"),
)
DESCRIBE_SQL = "SELECT column_name, column_type FROM (DESCRIBE {table})"
DISTINCT_SQL = "SELECT DISTINCT {column} FROM {table} ORDER BY 1"


def today(data_end: date | None = None) -> date:
    """DATA_END_DATE if set (the agent's "today"), else the real date."""
    return data_end or data_clock.data_end_from_env() or date.today()


def table_schemas(db_path: Path) -> str:
    """One line per standard table: name(column TYPE, ...), read from the database."""
    lines = []
    with sql_guard.open_readonly(db_path) as con:
        for table in TABLE_ORDER:
            # Table names come from the fixed tuple above, never from user input.
            columns = con.execute(DESCRIBE_SQL.format(table=table)).fetchall()
            lines.append(f"- {table}({', '.join(f'{name} {kind}' for name, kind in columns)})")
    return "\n".join(lines)


def column_values(db_path: Path) -> str:
    """The distinct values of a few text columns, e.g. orders.status: cancelled, ..."""
    lines = []
    with sql_guard.open_readonly(db_path) as con:
        for table, column in VALUE_COLUMNS:
            values = [
                row[0]
                for row in con.execute(DISTINCT_SQL.format(table=table, column=column)).fetchall()
            ]
            lines.append(f"- {table}.{column}: {', '.join(map(str, values))}")
    return "\n".join(lines)


def kpi_definitions(decisions_path: Path = DECISIONS_PATH) -> str:
    """The KPI table and period rules from docs/decisions.md, without its heading."""
    text = decisions_path.read_text(encoding="utf-8")
    start = text.index(KPI_SECTION_START)
    body_start = text.index("\n", start) + 1
    end = text.find(KPI_SECTION_END, body_start)
    return text[body_start : end if end != -1 else None].strip()


def load_examples(path: Path = EXAMPLES_PATH) -> list[dict]:
    """The example question/SQL pairs with their checked values."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def format_examples(examples: list[dict]) -> str:
    """Examples as 'Q: ...' followed by a SQL code block."""
    return "\n\n".join(
        f"Q: {example['question']}\n```sql\n{example['sql'].strip()}\n```" for example in examples
    )


def build_system_prompt(db_path: Path = kpis.DB_PATH, data_end: date | None = None) -> str:
    """The full system prompt for one agent run."""
    end = data_end or data_clock.data_end_from_env()
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.format(
        today=today(end).isoformat(),
        data_end=end.isoformat() if end else "the latest order date",
        kpi_definitions=kpi_definitions(),
        schemas=table_schemas(db_path),
        column_values=column_values(db_path),
        examples=format_examples(load_examples()),
    )
