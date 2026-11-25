"""Mock Northwind network + device system: a pretend external API over the SQLite DB.

This stands in for the carrier's network/provisioning platform (lines) and a device-management
view of the customer's handset (devices). There's no free real version of either, so it's mocked.

It is a dumb state store with validation, NOT your agent's tools: it doesn't know business rules
like "only resume a line if the bill is paid". Your tools call this API and enforce those rules.

Run:
    NORTHWIND_DB=northwind.db uvicorn mock_network.app:app --port 8300
    # docs at http://localhost:8300/docs

Information asymmetry: send `X-Actor: agent` from agent-side code. Device endpoints refuse that
header with 403, because only the customer can see or touch their phone. (`X-Actor: user` or no
header is allowed.)
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from northwind.db import DEFAULT_DB_PATH, connect

LINE_FIELDS = ["id", "customer_id", "msisdn", "plan_id", "status", "suspension_reason", "data_used_mb",
               "data_topup_mb", "provisioning_ok", "roaming_enabled"]
DEVICE_FIELDS = ["id", "line_id", "model", "os", "airplane_mode", "mobile_data_enabled", "data_roaming_on",
                 "is_abroad", "apn_ok", "needs_restart", "hardware_fault", "signal_bars"]
LINE_BOOLS = {"provisioning_ok", "roaming_enabled"}
DEVICE_BOOLS = {"airplane_mode", "mobile_data_enabled", "data_roaming_on", "is_abroad", "apn_ok",
                "needs_restart", "hardware_fault"}


class LinePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Optional[Literal["active", "suspended", "cancelled"]] = None
    suspension_reason: Optional[Literal["non_payment", "fraud_hold", "customer_request"]] = None
    plan_id: Optional[str] = None
    data_used_mb: Optional[int] = Field(None, ge=0)
    data_topup_mb: Optional[int] = Field(None, ge=0)
    provisioning_ok: Optional[bool] = None
    roaming_enabled: Optional[bool] = None


class DevicePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    airplane_mode: Optional[bool] = None
    mobile_data_enabled: Optional[bool] = None
    data_roaming_on: Optional[bool] = None
    is_abroad: Optional[bool] = None
    apn_ok: Optional[bool] = None
    needs_restart: Optional[bool] = None
    hardware_fault: Optional[bool] = None
    signal_bars: Optional[int] = Field(None, ge=0, le=5)


def db_path() -> Path:
    return Path(os.environ.get("NORTHWIND_DB", str(DEFAULT_DB_PATH)))


def get_conn():
    path = db_path()
    if not path.exists():
        raise HTTPException(503, f"database {path} not found; run python -m northwind.seed or load a scenario")
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()


def no_agent(x_actor: Optional[str] = Header(default=None)) -> None:
    if (x_actor or "").lower() == "agent":
        raise HTTPException(403, "device state is only visible to the customer; agent access denied")


def to_dict(row: sqlite3.Row, fields: list[str], bools: set[str]) -> dict:
    return {f: (bool(row[f]) if f in bools else row[f]) for f in fields}


def fetch(conn, table: str, key: str, value: str):
    return conn.execute(f"SELECT * FROM {table} WHERE {key} = ?", (value,)).fetchone()


def apply_patch(conn, table: str, row_id: str, patch: BaseModel) -> None:
    # exclude_unset keeps explicit nulls (e.g. suspension_reason: null when resuming a line)
    changes = patch.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "empty patch")
    values = {k: (int(v) if isinstance(v, bool) else v) for k, v in changes.items()}
    sets = ", ".join(f"{k} = ?" for k in values)
    try:
        conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*values.values(), row_id))
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        # e.g. status='active' with a suspension_reason, or an unknown plan_id
        raise HTTPException(409, f"rejected by network system: {e}") from e


app = FastAPI(title="Northwind mock network & device API", version="1.0")


@app.get("/health")
def health():
    return {"ok": True, "db": str(db_path())}


@app.get("/lines")
def find_lines(msisdn: Optional[str] = None, customer_id: Optional[str] = None, conn=Depends(get_conn)):
    if not msisdn and not customer_id:
        raise HTTPException(422, "pass msisdn or customer_id")
    q, args = "SELECT * FROM lines WHERE 1=1", []
    if msisdn:
        q, args = q + " AND msisdn = ?", args + [msisdn]
    if customer_id:
        q, args = q + " AND customer_id = ?", args + [customer_id]
    return [to_dict(r, LINE_FIELDS, LINE_BOOLS) for r in conn.execute(q + " ORDER BY id", args)]


@app.get("/lines/{line_id}")
def get_line(line_id: str, conn=Depends(get_conn)):
    row = fetch(conn, "lines", "id", line_id)
    if row is None:
        raise HTTPException(404, f"line {line_id} not found")
    return to_dict(row, LINE_FIELDS, LINE_BOOLS)


@app.patch("/lines/{line_id}")
def patch_line(line_id: str, patch: LinePatch, conn=Depends(get_conn)):
    if fetch(conn, "lines", "id", line_id) is None:
        raise HTTPException(404, f"line {line_id} not found")
    apply_patch(conn, "lines", line_id, patch)
    return to_dict(fetch(conn, "lines", "id", line_id), LINE_FIELDS, LINE_BOOLS)


@app.get("/lines/{line_id}/device", dependencies=[Depends(no_agent)])
def get_device_for_line(line_id: str, conn=Depends(get_conn)):
    row = fetch(conn, "devices", "line_id", line_id)
    if row is None:
        raise HTTPException(404, f"no device on line {line_id}")
    return to_dict(row, DEVICE_FIELDS, DEVICE_BOOLS)


@app.get("/devices/{device_id}", dependencies=[Depends(no_agent)])
def get_device(device_id: str, conn=Depends(get_conn)):
    row = fetch(conn, "devices", "id", device_id)
    if row is None:
        raise HTTPException(404, f"device {device_id} not found")
    return to_dict(row, DEVICE_FIELDS, DEVICE_BOOLS)


@app.patch("/devices/{device_id}", dependencies=[Depends(no_agent)])
def patch_device(device_id: str, patch: DevicePatch, conn=Depends(get_conn)):
    if fetch(conn, "devices", "id", device_id) is None:
        raise HTTPException(404, f"device {device_id} not found")
    apply_patch(conn, "devices", device_id, patch)
    return to_dict(fetch(conn, "devices", "id", device_id), DEVICE_FIELDS, DEVICE_BOOLS)
