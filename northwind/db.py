"""Database helpers: open a connection, create the schema, insert reference data."""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schema.sql"
DEFAULT_DB_PATH = ROOT / "northwind.db"

# Reference data: the plans Northwind sells. Seeded into every database.
PLANS = [
    {"id": "PLN-BASIC", "name": "Basic 5GB", "monthly_price_cents": 2500, "data_cap_mb": 5_120, "roaming_included": 0},
    {"id": "PLN-PLUS", "name": "Plus 20GB", "monthly_price_cents": 4000, "data_cap_mb": 20_480, "roaming_included": 0},
    {"id": "PLN-UNLTD", "name": "Unlimited", "monthly_price_cents": 6500, "data_cap_mb": 1_048_576, "roaming_included": 1},
]

# Reference data: cell sites. Scenarios can override a cell (e.g. put it in outage).
CELLS = [
    {"id": "CELL-MAN-0412", "name": "Midtown West 42nd St", "area": "Manhattan", "rat": "5G", "status": "up",
     "outage_reason": None, "outage_started_at": None, "estimated_restore_at": None},
    {"id": "CELL-BKN-0219", "name": "Williamsburg Bedford Ave", "area": "Brooklyn", "rat": "LTE", "status": "up",
     "outage_reason": None, "outage_started_at": None, "estimated_restore_at": None},
    {"id": "CELL-QNS-0107", "name": "Astoria Ditmars Blvd", "area": "Queens", "rat": "5G", "status": "up",
     "outage_reason": None, "outage_started_at": None, "estimated_restore_at": None},
    {"id": "CELL-JC-0033", "name": "Jersey City Exchange Pl", "area": "Jersey City", "rat": "LTE", "status": "up",
     "outage_reason": None, "outage_started_at": None, "estimated_restore_at": None},
]

TABLES = ["plans", "cells", "customers", "lines", "bills", "devices", "tickets"]


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a connection with foreign keys on and rows accessible by column name."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())


def insert_rows(conn: sqlite3.Connection, table: str, rows: list[dict], replace: bool = False) -> None:
    """Insert a list of dicts into `table`. Column names come from the dict keys.
    replace=True uses INSERT OR REPLACE (used to override reference rows like cells)."""
    if table not in TABLES:
        raise ValueError(f"unknown table: {table}")
    for row in rows:
        cols = list(row)
        placeholders = ", ".join("?" for _ in cols)
        conn.execute(
            f"INSERT {'OR REPLACE ' if replace else ''}INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
            [row[c] for c in cols],
        )


def insert_plans(conn: sqlite3.Connection) -> None:
    """Insert the reference data every DB starts with: plans and cells."""
    insert_rows(conn, "plans", PLANS)
    insert_rows(conn, "cells", CELLS)


def fresh_db(db_path: str | Path = ":memory:") -> sqlite3.Connection:
    """Create an empty database with the schema and reference data (plans, cells). Deletes an existing file first."""
    if str(db_path) != ":memory:":
        Path(db_path).unlink(missing_ok=True)
    conn = connect(db_path)
    create_schema(conn)
    insert_plans(conn)
    conn.commit()
    return conn
