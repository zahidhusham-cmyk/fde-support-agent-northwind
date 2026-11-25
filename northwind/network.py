"""The carrier's network-side view of a line, DERIVED from line + device + cell state.

Carriers can't see a phone's settings, but their core network does see what the phone does:
whether it's registered, on which cell, with what signal, whether a data session is up, and
when it was last heard from. This module computes that view so it always stays consistent with
the hidden device state, even after the customer changes their phone.

The key realism rule: the view shows SYMPTOMS, never device-side CAUSES. Airplane mode, a phone
that's switched off, an unseated SIM, a radio that needs a restart and water damage all look
the same from here: "not registered, last seen <time>". Likewise "registered but no data
session, no error" could be mobile data switched off or simply nothing using data.

What the network CAN attribute (because the cause is on the carrier side):
  - registration rejected  -> the line is suspended/cancelled, or roaming isn't allowed abroad
  - data blocked by policy -> data cap reached
  - data session error     -> unknown APN (bad provisioning / bad handset APN)
  - no coverage            -> the serving cell is in outage (visible in the cells table)

Usage:
    from northwind.network import get_network_status
    get_network_status(conn, "LIN-2001")
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

# Approximate LTE RSRP (dBm) reported for each signal bar count.
RSRP_BY_BARS = {1: -118, 2: -110, 3: -100, 4: -90, 5: -80}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def radio_reaches_network(device: dict, cell: dict | None) -> bool:
    """Ground truth (hidden): is the handset's radio actually in contact with a network?"""
    if not device["powered_on"] or device["airplane_mode"] or not device["sim_ok"]:
        return False
    if device["needs_restart"] or device["hardware_fault"] or device["signal_bars"] <= 0:
        return False
    if device["is_abroad"]:
        return True  # a partner network covers them
    return cell is not None and cell["status"] != "outage"


def derive_network_status(line: dict, device: dict, plan: dict, cell: dict | None, now: str | None = None) -> dict:
    """Pure function. Inputs are plain dicts (or sqlite3.Row) of one line, its device, plan and cell."""
    now = now or utc_now()
    abroad = bool(device["is_abroad"])
    reachable = radio_reaches_network(device, cell)

    if not reachable:
        registration, reject_reason = "not_registered", None
    elif line["status"] != "active":
        registration, reject_reason = "rejected", f"line_{line['status']}"
    elif abroad and not line["roaming_enabled"]:
        registration, reject_reason = "rejected", "roaming_not_allowed"
    else:
        registration, reject_reason = "registered", None
    registered = registration == "registered"

    bars = None
    rat = "none"
    if registered:
        bars = device["signal_bars"]
        if abroad:
            rat = "LTE"
        else:
            rat = cell["rat"]
            if cell["status"] == "degraded":
                rat, bars = "LTE", min(bars, 2)

    cap_mb = plan["data_cap_mb"] + line["data_topup_mb"]
    policy_block = "cap_reached" if line["data_used_mb"] >= cap_mb else None
    requesting_data = registered and device["mobile_data_enabled"] and (not abroad or device["data_roaming_on"])
    apn_good = device["apn_ok"] and line["provisioning_ok"]
    session_error = "unknown_apn" if requesting_data and not apn_good else None
    data_session_active = bool(requesting_data and apn_good and policy_block is None)

    return {
        "line_id": line["id"],
        "registration_state": registration,           # registered | rejected | not_registered
        "registered": registered,
        "reject_reason": reject_reason,               # line_suspended | line_cancelled | roaming_not_allowed | None
        "rat": rat,                                   # 5G | LTE | none
        "signal_bars": bars,
        "rsrp_dbm": RSRP_BY_BARS.get(bars) if bars else None,
        "serving_cell_id": (None if abroad else device["cell_id"]) if registered else None,
        "roaming_network": device["visited_network"] if registered and abroad else None,
        # Last cell the network heard the phone on (known even after it drops off). Lets the agent
        # spot an outage on the customer's tower without knowing anything about the phone itself.
        "last_cell_id": None if abroad or not (reachable or device["last_attached_at"]) else device["cell_id"],
        "last_seen": now if reachable else device["last_attached_at"],
        "data_session_active": data_session_active,
        "data_session_error": session_error,          # unknown_apn | None
        "data_policy_block": policy_block,            # cap_reached | None
        "data_used_mb": line["data_used_mb"],
        "data_allowance_mb": cap_mb,
    }


def _load(conn: sqlite3.Connection, line_id: str):
    line = conn.execute("SELECT * FROM lines WHERE id = ?", (line_id,)).fetchone()
    if line is None:
        raise KeyError(f"line {line_id} not found")
    device = conn.execute("SELECT * FROM devices WHERE line_id = ?", (line_id,)).fetchone()
    if device is None:
        raise KeyError(f"no device on line {line_id}")
    plan = conn.execute("SELECT * FROM plans WHERE id = ?", (line["plan_id"],)).fetchone()
    cell = conn.execute("SELECT * FROM cells WHERE id = ?", (device["cell_id"],)).fetchone() if device["cell_id"] else None
    return line, device, plan, cell


def get_network_status(conn: sqlite3.Connection, line_id: str, now: str | None = None) -> dict:
    """The network view of one line. Raises KeyError if the line or its device doesn't exist."""
    return derive_network_status(*_load(conn, line_id), now=now)


def data_works(conn: sqlite3.Connection, line_id: str) -> bool:
    """Ground truth for 'can the customer use mobile data right now?'"""
    return get_network_status(conn, line_id)["data_session_active"]


def mark_last_seen(conn: sqlite3.Connection, device_ids: list[str] | None = None, now: str | None = None) -> None:
    """Stamp last_attached_at=now on devices whose radio currently reaches the network.

    Call this BEFORE applying a change that might take a phone off the network (airplane mode on,
    a cell going into outage, ...), so 'last seen' afterwards is the moment it dropped off.
    """
    now = now or utc_now()
    q = "SELECT d.*, c.status AS c_status FROM devices d LEFT JOIN cells c ON c.id = d.cell_id"
    rows = conn.execute(q).fetchall()
    for d in rows:
        if device_ids is not None and d["id"] not in device_ids:
            continue
        cell = {"status": d["c_status"]} if d["c_status"] else None
        if radio_reaches_network(d, cell):
            conn.execute("UPDATE devices SET last_attached_at = ? WHERE id = ?", (now, d["id"]))
