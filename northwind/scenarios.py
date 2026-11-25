"""Load a scenario into a fresh SQLite database and check the end state.

    from northwind.scenarios import list_scenarios, load_scenario, check_end_state

    scenario = load_scenario_file("s01_airplane_mode")
    conn = load_scenario(scenario)          # fresh in-memory DB with the initial state
    ...                                     # your agent + simulated user change the DB here
    result = check_end_state(conn, scenario)
    print(result.passed, result.failures)

Expected end state format (in each scenario JSON):

    "expected_end_state": {
        "<table>": {
            "<primary key>": {
                "<column>": <value>                       # exact match (null matches NULL)
                "<column>": {"op": "gte", "value": 1024}  # or an operator
            }
        }
    }

Operators: eq, ne, gt, gte, lt, lte, in, not_in, is_null, not_null.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

from northwind.db import TABLES, fresh_db, insert_rows

SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"
# Order matters for foreign keys.
INSERT_ORDER = ["customers", "lines", "bills", "devices", "tickets"]

OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: a is not None and a > b,
    "gte": lambda a, b: a is not None and a >= b,
    "lt": lambda a, b: a is not None and a < b,
    "lte": lambda a, b: a is not None and a <= b,
    "in": lambda a, b: a in b,
    "not_in": lambda a, b: a not in b,
    "is_null": lambda a, _b: a is None,
    "not_null": lambda a, _b: a is not None,
}


@dataclass
class CheckResult:
    scenario_id: str
    passed: bool
    failures: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        head = f"{self.scenario_id}: {'PASS' if self.passed else 'FAIL'}"
        return "\n".join([head, *(f"  - {f}" for f in self.failures)])


def list_scenarios() -> list[str]:
    """Scenario ids, sorted (e.g. ['s01_airplane_mode', ...])."""
    return sorted(p.stem for p in SCENARIO_DIR.glob("*.json"))


def load_scenario_file(scenario_id_or_path: str | Path) -> dict:
    """Read a scenario JSON by id ('s01_airplane_mode') or by file path."""
    path = Path(scenario_id_or_path)
    if not path.suffix:
        path = SCENARIO_DIR / f"{scenario_id_or_path}.json"
    return json.loads(path.read_text())


def load_scenario(scenario: dict | str, db_path: str | Path = ":memory:") -> sqlite3.Connection:
    """Create a fresh DB (schema + plans) and insert the scenario's initial state."""
    if not isinstance(scenario, dict):
        scenario = load_scenario_file(scenario)
    conn = fresh_db(db_path)
    state = scenario["initial_state"]
    unknown = set(state) - set(INSERT_ORDER)
    if unknown:
        raise ValueError(f"initial_state has unknown tables: {sorted(unknown)}")
    for table in INSERT_ORDER:
        insert_rows(conn, table, state.get(table, []))
    conn.commit()
    return conn


def _matches(actual, expected) -> tuple[bool, str]:
    if isinstance(expected, dict) and "op" in expected:
        op = expected["op"]
        if op not in OPS:
            raise ValueError(f"unknown op: {op}")
        value = expected.get("value")
        desc = op if op in ("is_null", "not_null") else f"{op} {value!r}"
        return OPS[op](actual, value), desc
    return actual == expected, f"== {expected!r}"


def check_end_state(conn: sqlite3.Connection, scenario: dict | str) -> CheckResult:
    """Compare the DB to the scenario's expected_end_state. Returns every mismatch, not just the first."""
    if not isinstance(scenario, dict):
        scenario = load_scenario_file(scenario)
    failures: list[str] = []
    for table, rows in scenario["expected_end_state"].items():
        if table not in TABLES:
            raise ValueError(f"expected_end_state has unknown table: {table}")
        for pk, columns in rows.items():
            row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (pk,)).fetchone()
            if row is None:
                failures.append(f"{table}.{pk}: row missing")
                continue
            for column, expected in columns.items():
                if column not in row.keys():
                    raise ValueError(f"{table} has no column {column}")
                ok, desc = _matches(row[column], expected)
                if not ok:
                    failures.append(f"{table}.{pk}.{column}: expected {desc}, got {row[column]!r}")
    return CheckResult(scenario["id"], not failures, failures)


def main() -> None:
    """python -m northwind.scenarios            -> list scenarios
       python -m northwind.scenarios <id> [db]  -> load one (into db file if given) and show its check"""
    if len(sys.argv) == 1:
        for sid in list_scenarios():
            s = load_scenario_file(sid)
            print(f"{sid:<30} {s['difficulty']:<7} {s['title']}")
        return
    sid = sys.argv[1]
    db_path = sys.argv[2] if len(sys.argv) > 2 else ":memory:"
    scenario = load_scenario_file(sid)
    conn = load_scenario(scenario, db_path)
    print(f"Loaded {sid} into {db_path}")
    print(f"Opening message: {scenario['opening_message']}")
    print(check_end_state(conn, scenario))


if __name__ == "__main__":
    main()
