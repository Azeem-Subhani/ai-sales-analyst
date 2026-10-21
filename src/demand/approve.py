"""Approve or reject a pre-build recommendation (Week 5).

    python -m src.demand.approve --id 20260930-SB-1042 --approve --by "A. Planner" [--note "..."]

Only a pending recommendation can be decided, and only once. Each decision updates the
recommendation's status and appends a row to prebuild_decisions (who, when, note).
This is the only code that changes a status: the agent tools and the reports never
import it. Week 6 adds Streamlit buttons on top of decide().
"""

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.demand import store

DECISIONS = {"approve": "approved", "reject": "rejected"}
MAX_NOTE_CHARS = 500

RECOMMENDATION_SQL = """
SELECT rec_id, as_of, sku, recommended_units, status
FROM prebuild_recommendations WHERE rec_id = $rec_id
"""
UPDATE_SQL = """
UPDATE prebuild_recommendations
SET status = $status, decided_by = $by, decided_at_utc = $at, note = $note
WHERE rec_id = $rec_id AND status = 'pending'
"""
INSERT_DECISION_SQL = """
INSERT INTO prebuild_decisions
    (rec_id, as_of, sku, recommended_units, decision, decided_by, decided_at_utc, note)
VALUES ($rec_id, $as_of, $sku, $units, $status, $by, $at, $note)
"""


class ApprovalError(Exception):
    """The decision cannot be recorded (unknown id, already decided, bad input)."""


@dataclass
class Decision:
    """A recorded decision."""

    rec_id: str
    status: str
    decided_by: str
    decided_at_utc: datetime
    note: str | None


def decide(
    rec_id: str,
    decision: str,
    decided_by: str,
    note: str | None = None,
    planning_db: Path = store.PLANNING_DB_PATH,
) -> Decision:
    """Record approve/reject for one pending recommendation, in one transaction."""
    if decision not in DECISIONS:
        raise ApprovalError(f"decision must be one of {sorted(DECISIONS)}; got {decision!r}.")
    decided_by = (decided_by or "").strip()
    if not decided_by:
        raise ApprovalError("--by must name the person deciding.")
    if note is not None and len(note) > MAX_NOTE_CHARS:
        raise ApprovalError(f"--note is limited to {MAX_NOTE_CHARS} characters.")
    if not Path(planning_db).exists():
        raise ApprovalError(f"{planning_db} does not exist; run python -m src.demand.run first.")

    status = DECISIONS[decision]
    at = datetime.now(UTC).replace(tzinfo=None)
    with store._write_lock:
        con = store.connect_write(Path(planning_db))
        in_transaction = False
        try:
            row = con.execute(RECOMMENDATION_SQL, {"rec_id": rec_id}).fetchone()
            if row is None:
                raise ApprovalError(f"No pre-build recommendation with id {rec_id!r}.")
            _, as_of, sku, units, current = row
            if current != "pending":
                raise ApprovalError(f"{rec_id} is already {current}; a decision is final.")
            params = {"rec_id": rec_id, "status": status, "by": decided_by, "at": at, "note": note}
            con.execute("BEGIN TRANSACTION")
            in_transaction = True
            con.execute(UPDATE_SQL, params)
            con.execute(INSERT_DECISION_SQL, {**params, "as_of": as_of, "sku": sku, "units": units})
            con.execute("COMMIT")
        except Exception:
            if in_transaction:
                con.execute("ROLLBACK")
            raise
        finally:
            con.close()
    return Decision(rec_id, status, decided_by, at, note)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description="Approve or reject a pre-build recommendation.")
    parser.add_argument("--id", required=True, dest="rec_id", help="recommendation id")
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--approve", action="store_const", const="approve", dest="decision")
    choice.add_argument("--reject", action="store_const", const="reject", dest="decision")
    parser.add_argument("--by", required=True, help="name of the person deciding")
    parser.add_argument("--note", help="optional note")
    parser.add_argument("--planning-db", type=Path, default=store.PLANNING_DB_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Record one decision and print it."""
    args = parse_args(argv)
    try:
        result = decide(args.rec_id, args.decision, args.by, args.note, args.planning_db)
    except (ApprovalError, store.PlanningStoreError) as error:
        raise SystemExit(f"error: {error}") from error
    print(f"{result.rec_id}: {result.status} by {result.decided_by} at {result.decided_at_utc} UTC")


if __name__ == "__main__":
    main()
