"""Fill the gold values in tests/eval/questions.yaml by running each question's gold_sql.

No LLM call. Every gold_sql runs through the agent's SQL guard on a read-only
connection, so gold values come from SQL only, never from a person or a model.
Demand-planning questions (Week 5) use gold_planning_sql instead: SQL over the stored
demand snapshot in planning.duckdb (next to the sales database), opened read-only.
Run python -m src.demand.run --as-of 2026-09-30 first.

    python scripts/fill_gold.py           # rewrite questions.yaml with fresh gold values
    python scripts/fill_gold.py --check   # exit 1 if any stored gold value is out of date
"""

import argparse
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))  # so "python scripts/fill_gold.py" finds src/

import duckdb

from src.agent import eval_grader, sql_guard
from src.analytics import kpis
from src.demand import store

QUESTIONS_PATH = PROJECT_ROOT / "tests" / "eval" / "questions.yaml"
HEADER_END = "\n\n- id:"  # the header comment ends where the first question starts


def gold_for(item: dict, db_path: Path) -> dict:
    """Run one gold_sql (or gold_planning_sql) and turn its rows into gold numbers and names."""
    if "gold_planning_sql" in item:
        columns, raw = planning_rows(item["gold_planning_sql"], store.planning_path_for(db_path))
    else:
        result = sql_guard.run_query(item["gold_sql"], db_path)
        columns, raw = result.columns, result.rows
    rows = [[plain(v) for v in row] for row in raw]
    return eval_grader.gold_from_rows(item["grade"], columns, rows)


def planning_rows(sql: str, planning_db: Path) -> tuple[list[str], list[tuple]]:
    """Run gold SQL on the planning database, read-only."""
    if not planning_db.exists():
        raise SystemExit(f"{planning_db} not found; run python -m src.demand.run first.")
    with duckdb.connect(str(planning_db), read_only=True) as con:
        cursor = con.execute(sql)
        return [d[0] for d in cursor.description], cursor.fetchall()


def plain(value: object) -> object:
    """Decimals as floats, everything else unchanged."""
    return float(value) if hasattr(value, "as_tuple") else value


def fill(questions: list[dict], db_path: Path) -> list[dict]:
    """Every question (and alternative) with its gold block recomputed."""
    for question in questions:
        if question.get("check") == eval_grader.OFF_TOPIC:
            continue  # no gold SQL: graded by the off-topic rules
        for item in question.get("alternatives") or [question]:
            item["gold"] = gold_for(item, db_path)
    return questions


def literal_strings(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    """Write multi-line strings (the SQL) as readable | blocks."""
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


def dump(questions: list[dict], header: str) -> str:
    """The YAML file text: the original header comment, then the questions."""
    dumper = yaml.SafeDumper
    dumper.add_representer(str, literal_strings)
    body = yaml.dump(questions, Dumper=dumper, sort_keys=False, allow_unicode=True, width=100)
    return header + "\n\n" + body.replace("\n- id:", "\n\n- id:")


def main(argv: list[str] | None = None) -> int:
    """Fill or check the gold values."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only check, do not write")
    parser.add_argument("--db", type=Path, default=kpis.DB_PATH)
    parser.add_argument("--path", type=Path, default=QUESTIONS_PATH)
    args = parser.parse_args(argv)

    text = args.path.read_text(encoding="utf-8")
    header = text[: text.index(HEADER_END)]
    stored = yaml.safe_load(text)
    fresh = fill(yaml.safe_load(text), args.db)
    if args.check:
        stale = [q["id"] for q, f in zip(stored, fresh, strict=True) if q != f]
        if stale:
            print(f"Gold values out of date for: {', '.join(stale)}. Run scripts/fill_gold.py.")
            return 1
        print(f"All {len(fresh)} questions have up-to-date gold values.")
        return 0
    args.path.write_text(dump(fresh, header), encoding="utf-8")
    print(f"Wrote gold values for {len(fresh)} questions to {args.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
