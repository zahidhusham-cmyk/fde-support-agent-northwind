"""Trace store for one agent dispatch.

Lives in runs.db, not northwind.db. fresh_db() deletes the world file on every
seed, and that must not take the traces with it.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from northwind.db import ROOT

DEFAULT_TRACE_PATH = ROOT / "runs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id                TEXT PRIMARY KEY,
    incident_number   TEXT,
    correlation_id    TEXT,
    model             TEXT NOT NULL,
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    stop_reason       TEXT CHECK (stop_reason IN (
                          'max_turns', 'interrupted', 'resolved', 'escalated', 'error'
                      ))
);

CREATE TABLE IF NOT EXISTS run_steps (
    id                INTEGER PRIMARY KEY,
    run_id            TEXT NOT NULL REFERENCES runs(id),
    step_index        INTEGER NOT NULL,
    ts                TEXT NOT NULL,
    actor             TEXT NOT NULL CHECK (actor IN ('model', 'tool', 'agent', 'user')),
    message           TEXT,
    tool_name         TEXT,
    arguments_json    TEXT,
    result_json       TEXT,
    latency_ms        INTEGER,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    UNIQUE (run_id, step_index)
);
"""


def connect(db_path: str | Path = DEFAULT_TRACE_PATH) -> sqlite3.Connection:
    """Open runs.db and create runs and run_steps if they are missing."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn

def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(value: str | None) -> str | None:
    """Strip a value. Missing or blank becomes None so the column stays null."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def record_start_run(row: dict) -> str:
    """Insert one open runs row and return its id.

    incident_number and correlation_id may be missing. finished_at and
    stop_reason stay null until the run ends. The id and started_at are also
    written back onto row for the caller.
    """
    model = _text(row.get("model"))
    if not model:
        raise ValueError("model is required")
    incident_number = _text(row.get("incident_number"))
    correlation_id = _text(row.get("correlation_id"))
    run_id = str(uuid.uuid4())
    started_at = utc_now()
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO runs (id, incident_number, correlation_id, model, started_at) VALUES (?, ?, ?, ?, ?)",
            (run_id, incident_number, correlation_id, model, started_at),
        )
        conn.commit()
    finally:
        conn.close()
    row["id"] = run_id
    row["incident_number"] = incident_number
    row["correlation_id"] = correlation_id
    row["model"] = model
    row["started_at"] = started_at
    return run_id


def _json_text(value) -> str | None:
    """Store a dict as JSON. A string is kept as-is."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    return json.dumps(value)


def _append_step(
    run_id: str,
    actor: str,
    message: str | None = None,
    tool_name: str | None = None,
    arguments_json: str | None = None,
    result_json: str | None = None,
    latency_ms: int | None = None,
) -> int:
    """Insert the next step for this run. Tool calls and messages share the counter."""
    conn = connect()
    try:
        step_index = conn.execute(
            "SELECT COALESCE(MAX(step_index), -1) + 1 FROM run_steps WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO run_steps
               (run_id, step_index, ts, actor, message, tool_name, arguments_json, result_json, latency_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, step_index, utc_now(), actor, message, tool_name, arguments_json, result_json, latency_ms),
        )
        conn.commit()
    finally:
        conn.close()
    return step_index


def record_message(run_id: str, actor: str, message: str) -> int:
    """Insert one user or agent line and return its step_index."""
    run_id = _text(run_id)
    actor = _text(actor)
    if not run_id:
        raise ValueError("run_id is required")
    if actor not in ("user", "agent"):
        raise ValueError("actor must be user or agent")
    text = "" if message is None else str(message).strip()
    if actor == "agent" and not text:
        raise ValueError("message is required")
    return _append_step(run_id, actor, message=text)


def record_tool_call(row: dict) -> int:
    """Insert one tool step for this run and return its step_index.

    run_id and name are required. parameters may be a dict or a JSON string.
    result is the JSON string from process_tool_call. latency is milliseconds.
    The step number is one past the last step stored for this run.
    """
    run_id = _text(row.get("run_id"))
    tool_name = _text(row.get("name") or row.get("tool_name"))
    if not run_id:
        raise ValueError("run_id is required")
    if not tool_name:
        raise ValueError("name is required")
    latency = row.get("latency_ms", row.get("latency"))
    if latency is not None:
        latency = int(latency)
    return _append_step(
        run_id,
        "tool",
        tool_name=tool_name,
        arguments_json=_json_text(row.get("parameters")),
        result_json=_json_text(row.get("result")),
        latency_ms=latency,
    )
