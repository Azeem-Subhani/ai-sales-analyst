"""SQL guard for the agent's run_sql and make_chart tools (plan sections 10 and 13).

A query is accepted only if all of these hold:
1. duckdb.extract_statements finds exactly one statement, of type SELECT, and its
   text starts with SELECT or WITH. (DuckDB reports PRAGMA, DESCRIBE, SHOW and
   SUMMARIZE as SELECTs too, so the type alone is not enough.)
2. With string literals and comments removed, no blocked keyword appears (PRAGMA,
   ATTACH, COPY, INSTALL, LOAD, SET, ...).
3. Walking DuckDB's own parse tree (json_serialize_sql), every table read is one
   of the six standard tables or a CTE defined in the query, every table function
   is on a short allowlist, and no file-reading or system function is called.

The connection the agent uses is also read-only with external access disabled
(see open_readonly), so a query that somehow slipped past the guard still could not
write, attach audit.duckdb or read files.
"""

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

STANDARD_TABLES = frozenset(
    {"customers", "products", "orders", "order_items", "inventory", "forecasts"}
)
ALLOWED_SCHEMAS = {"", "main"}
# Table functions that only generate values (date spines, lists); everything else
# in FROM (read_csv, duckdb_tables, query, ...) is refused.
ALLOWED_TABLE_FUNCTIONS = frozenset({"range", "generate_series", "unnest"})
BLOCKED_KEYWORDS = (
    "PRAGMA",
    "ATTACH",
    "DETACH",
    "COPY",
    "INSTALL",
    "LOAD",
    "SET",
    "RESET",
    "EXPORT",
    "IMPORT",
    "CALL",
    "CHECKPOINT",
    "VACUUM",
)
BLOCKED_KEYWORD = re.compile(r"\b(" + "|".join(BLOCKED_KEYWORDS) + r")\b", re.IGNORECASE)
# File and system access, wherever it appears (scalar or table function).
BLOCKED_FUNCTION = re.compile(
    r"^(read_\w+|\w+_scan|glob|query|query_table|getenv|sniff_csv|parquet_\w+|"
    r"duckdb_\w+|pragma_\w+|load_\w+|current_setting|which_secret)$",
    re.IGNORECASE,
)
FIRST_KEYWORD = re.compile(r"^\s*(\w+)")
STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
LINE_COMMENT = re.compile(r"--[^\n]*")
BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

MAX_ROWS = 500
DEFAULT_TIMEOUT_SECONDS = 10.0


class SQLGuardError(ValueError):
    """The query was refused; the message is written for the model to act on."""


class SQLTimeoutError(RuntimeError):
    """The query ran longer than the timeout and was interrupted."""


@dataclass
class QueryResult:
    """Rows of an accepted query, capped at MAX_ROWS."""

    columns: list[str]
    rows: list[list[Any]]
    truncated: bool

    @property
    def row_count(self) -> int:
        """Rows returned (at most MAX_ROWS)."""
        return len(self.rows)


# ---------------------------------------------------------------- guard


def check_sql(query: str) -> str:
    """Return the query (stripped) if it is allowed, else raise SQLGuardError."""
    query = query.strip().rstrip(";").strip()
    if not query:
        raise SQLGuardError("The query is empty.")
    try:
        statements = duckdb.extract_statements(query)
    except duckdb.Error as error:
        raise SQLGuardError(f"The query does not parse: {error}") from error
    if len(statements) != 1:
        raise SQLGuardError(
            f"Send exactly one SELECT statement; found {len(statements)} statements."
        )
    statement = statements[0]
    if statement.type != duckdb.StatementType.SELECT:
        raise SQLGuardError(
            f"Only SELECT queries are allowed; this is a {statement.type.name} statement."
        )
    code = code_only(query)
    first = FIRST_KEYWORD.match(code)
    if not first or first.group(1).upper() not in {"SELECT", "WITH"}:
        raise SQLGuardError("The query must start with SELECT or WITH.")
    blocked = BLOCKED_KEYWORD.search(code)
    if blocked:
        raise SQLGuardError(f"{blocked.group(1).upper()} is not allowed.")
    check_tree(query)
    return query


def code_only(query: str) -> str:
    """The query without comments and string literals (they may mention any word)."""
    query = BLOCK_COMMENT.sub(" ", query)
    query = LINE_COMMENT.sub(" ", query)
    return STRING_LITERAL.sub("''", query)


def check_tree(query: str) -> None:
    """Check tables and functions in DuckDB's parse tree of the query."""
    with duckdb.connect() as con:  # parsing only; no database is opened
        raw = con.execute("SELECT json_serialize_sql(?)", [query]).fetchone()[0]
    tree = json.loads(raw)
    if tree.get("error"):
        raise SQLGuardError(f"The query does not parse: {tree.get('error_message', '')}")
    cte_names = {name.lower() for name in collect_cte_names(tree)}
    for node in walk(tree):
        node_type = node.get("type")
        if node_type == "BASE_TABLE":
            check_table(node, cte_names)
        elif node_type == "TABLE_FUNCTION":
            name = function_name(node.get("function", {}))
            if name.lower() not in ALLOWED_TABLE_FUNCTIONS:
                raise SQLGuardError(
                    f"Table function {name}() is not allowed; query the standard tables."
                )
        elif node.get("class") == "FUNCTION":
            name = function_name(node)
            if BLOCKED_FUNCTION.match(name):
                raise SQLGuardError(f"Function {name}() is not allowed.")


def check_table(node: dict, cte_names: set[str]) -> None:
    """A table reference must be a CTE from the query or one of the standard tables."""
    name = node.get("table_name", "")
    schema = node.get("schema_name", "") or ""
    catalog = node.get("catalog_name", "") or ""
    if not catalog and not schema and name.lower() in cte_names:
        return
    if catalog or schema.lower() not in ALLOWED_SCHEMAS or name.lower() not in STANDARD_TABLES:
        shown = ".".join(part for part in (catalog, schema, name) if part)
        raise SQLGuardError(
            f"Table {shown!r} is not allowed. Use only: {', '.join(sorted(STANDARD_TABLES))}."
        )


def function_name(node: dict) -> str:
    """The function name of a FUNCTION expression node."""
    return str(node.get("function_name", ""))


def collect_cte_names(tree: Any) -> list[str]:
    """Every CTE name defined anywhere in the query."""
    names = []
    for node in walk(tree):
        cte_map = node.get("cte_map")
        if isinstance(cte_map, dict):
            names += [entry["key"] for entry in cte_map.get("map", [])]
    return names


def walk(value: Any) -> list[dict]:
    """Every dict in a nested JSON structure, depth first."""
    found: list[dict] = []
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            found.append(item)
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return found


# ---------------------------------------------------------------- running


def open_readonly(db_path: Path) -> duckdb.DuckDBPyConnection:
    """sales.duckdb read-only, no file or network access; audit.duckdb is never attached.

    External access is switched off with SET rather than a connect-time config,
    because DuckDB refuses a second in-process connection to the same file with a
    different config (Streamlit and the reports keep plain read-only connections).
    The setting applies to the whole in-process database and cannot be switched back
    on; nothing in the project reads files through DuckDB, so that is safe.
    """
    con = duckdb.connect(str(db_path), read_only=True)
    con.execute("SET enable_external_access = false")
    return con


def run_query(
    query: str,
    db_path: Path,
    *,
    max_rows: int = MAX_ROWS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> QueryResult:
    """Guard and run a query; return at most max_rows rows and whether more existed."""
    query = check_sql(query)
    with open_readonly(db_path) as con:
        timer = threading.Timer(timeout_seconds, con.interrupt)
        timer.start()
        try:
            cursor = con.execute(query)
            columns = [d[0] for d in cursor.description]
            rows = cursor.fetchmany(max_rows + 1)  # one extra row tells us it was cut
        except duckdb.InterruptException as error:
            raise SQLTimeoutError(
                f"The query took longer than {timeout_seconds:g} seconds and was stopped. "
                "Aggregate more or add filters."
            ) from error
        except duckdb.Error as error:  # a SQL mistake, e.g. an unknown column
            raise SQLGuardError(f"The query failed: {error}") from error
        finally:
            timer.cancel()
    truncated = len(rows) > max_rows
    return QueryResult(columns, [list(r) for r in rows[:max_rows]], truncated)
