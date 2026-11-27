
import json
import os
from datetime import datetime, timezone

import requests

from northwind.db import connect
from northwind.integrations import ConfigError, load_env, servicenow_config

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_customer",
            "description": "Get the customer information from the database by customer id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "The customer id, for example CUS-1001.",
                    }
                },
                "required": ["customer_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_billing",
            "description": "Get the customer billing information from the database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "The customer ID to get the billing information for.",
                    }
                },
                "required": ["customer_id"],
            },
        },
    },
    {
        "type": "function",
        "function" : {
            "name": "get_line_status",
            "description": "Look up a mobile line. Pass customer_id when you only know the account, and you get every line on it. Pass line_id when you already have one line, and you get that line only. If you pass both, line_id wins. Pass at least one.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "Account id, for example CUS-1001. Returns every line on that account.",
                    },
                    "line_id": {
                        "type": "string",
                        "description": "Line id, for example LIN-2001. Returns that one line.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_network_status",
            "description": "What the carrier network sees for one line: registration, signal, whether a data session is up, and any policy block. This does not reveal phone settings such as airplane mode.",
            "parameters": {
                "type": "object",
                "properties": {
                    "line_id": {
                        "type": "string",
                        "description": "The line id, for example LIN-2001.",
                    }
                },
                "required": ["line_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_area_outages",
            "description": "List cell towers currently in outage. Call this with no arguments to see every outage. Pass area only to limit the list to one place name, such as Queens. Compare a line's last_cell_id from get_network_status to these tower ids.",
            "parameters": {
                "type": "object",
                "properties": {
                    "area": {
                        "type": "string",
                        "description": "Optional place name, for example Queens. Not an id. Omit it to return every outage.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pay_bill",
            "description": "Pay a bill. Pass bill_id. The agent can only pay one bill at a time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bill_id": {
                        "type": "string",
                        "description": "The bill id, for example BIL-3001.",
                    }
                },
                "required": ["bill_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resume_line",
            "description": "Resume a suspended line. Refuses a fraud hold, and refuses a non-payment suspension while any bill is still overdue. Pass line_id.",
            "parameters": {
                "type": "object",
                "properties":{
                    "line_id": {
                        "type": "string",
                        "description": "The line id, for example LIN-2001.",
                    }
                },
                "required": ["line_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_work_note",
            "description": "Add one internal work note on the ServiceNow incident. The customer does not see it. Pass incident_number and note.",
            "parameters": {
                "type": "object",
                "properties": {
                    "incident_number": {
                        "type": "string",
                        "description": "ServiceNow incident number from the ticket text, for example INC0010001.",
                    },
                    "note": {
                        "type": "string",
                        "description": "What you found or changed. One note per call.",
                    },
                },
                "required": ["incident_number", "note"],
            },
        },
    },
    {
        "type": "function",
        "function":{
            "name": "resolve_ticket",
            "description": "Mark the ServiceNow incident resolved after the data works or the cause is a tower outage. Pass incident_number and a short resolution that names the cause.",
            "parameters": {
                "type": "object",
                "properties": {
                    "incident_number": {
                        "type": "string",
                        "description": "ServiceNow incident number from the ticket text, for example INC0010001.",
                    },
                    "resolution": {
                        "type": "string",
                        "description": "What was wrong and what fixed it. For a tower outage, say outage.",
                    },
                },
                "required": ["incident_number", "resolution"],
            },
        }
    }
]

################################################################################
def process_tool_call(tool_call: dict) -> str:
    """Run one tool and return JSON text for the next model message.

    The lookup functions still return dicts. This is the only place that
    turns them into a string the chat API can put in a tool message.
    """
    parameters = tool_call.get("parameters") or {}
    if tool_call["name"] == "get_customer":
        result = get_customer(parameters["customer_id"])
    elif tool_call["name"] == "get_billing":
        result = get_billing(parameters["customer_id"])
    elif tool_call["name"] == "get_line_status":
        result = get_line_status(
            customer_id=parameters.get("customer_id"),
            line_id=parameters.get("line_id"),
        )
    elif tool_call["name"] == "get_network_status":
        result = get_network_status(parameters["line_id"])
    elif tool_call["name"] == "check_area_outages":
        result = check_area_outages(parameters.get("area"))
    elif tool_call["name"] == "pay_bill":
        result = pay_bill(parameters["bill_id"])
    elif tool_call["name"] == "resume_line":
        result = resume_line(parameters["line_id"])
    elif tool_call["name"] == "add_work_note":
        result = add_work_note(parameters.get("incident_number", ""), parameters.get("note", ""))
    elif tool_call["name"] == "resolve_ticket":
        result = resolve_ticket(parameters.get("incident_number", ""), parameters.get("resolution", ""))
    else:
        result = {"error": f"Unknown tool call: {tool_call['name']}"}
    return json.dumps(result)

################################################################################
def get_customer(customer_id: str) -> dict:
    """Customer row from SQLite. The ticket carries customer_id, not the name."""
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"error": f"customer {customer_id} not found"}
    return dict(row)

################################################################################
def get_billing(customer_id: str) -> dict:
    """Bills for one customer from SQLite, oldest period first."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM bills WHERE customer_id = ? ORDER BY period", (customer_id,)
        ).fetchall()
    finally:
        conn.close()
    return {"customer_id": customer_id, "bills": [dict(row) for row in rows]}

################################################################################
def get_line_status(customer_id: str | None = None, line_id: str | None = None):
    """Line status from the mock network API.

    customer_id lists every line on the account. line_id returns that one line.
    A line id wins when both are passed.
    """
    if not customer_id and not line_id:
        return {"error": "pass customer_id or line_id"}
    load_env()
    base = os.getenv("NETWORK_API_URL", "http://localhost:8300").rstrip("/")
    headers = {"X-Actor": "agent", "Accept": "application/json"}
    try:
        if line_id:
            response = requests.get(f"{base}/lines/{line_id}", headers=headers, timeout=30)
        else:
            response = requests.get(
                f"{base}/lines", params={"customer_id": customer_id}, headers=headers, timeout=30
            )
    except requests.RequestException as exc:
        return {"error": f"network API unreachable at {base}: {exc}"}
    if response.status_code == 404:
        return {"error": f"line {line_id} not found"}
    if response.status_code != 200:
        return {"error": f"network API returned {response.status_code}: {response.text[:200]}"}
    return response.json()

################################################################################
def get_network_status(line_id: str) -> dict:
    """Carrier network view for one line. Does not read the devices table.

    GET /lines/{line_id}/network on the mock API. The agent uses this to see
    symptoms (registered or not, data session up) without the phone settings
    that caused them.
    """
    if not line_id:
        return {"error": "pass line_id"}
    load_env()
    base = os.getenv("NETWORK_API_URL", "http://localhost:8300").rstrip("/")
    headers = {"X-Actor": "agent", "Accept": "application/json"}
    try:
        response = requests.get(f"{base}/lines/{line_id}/network", headers=headers, timeout=30)
    except requests.RequestException as exc:
        return {"error": f"network API unreachable at {base}: {exc}"}
    if response.status_code == 404:
        return {"error": f"line {line_id} not found"}
    if response.status_code != 200:
        return {"error": f"network API returned {response.status_code}: {response.text[:200]}"}
    return response.json()

################################################################################
def check_area_outages(area: str | None = None) -> dict:
    """Towers currently in outage, from GET /cells?status=outage.

    area is an optional place name such as Queens, not an id. The agent compares
    a line's last_cell_id to the ids in this list.
    """
    load_env()
    base = os.getenv("NETWORK_API_URL", "http://localhost:8300").rstrip("/")
    headers = {"X-Actor": "agent", "Accept": "application/json"}
    try:
        response = requests.get(f"{base}/cells", params={"status": "outage"}, headers=headers, timeout=30)
    except requests.RequestException as exc:
        return {"error": f"network API unreachable at {base}: {exc}"}
    if response.status_code != 200:
        return {"error": f"network API returned {response.status_code}: {response.text[:200]}"}
    outages = response.json()
    if area:
        wanted = area.strip().lower()
        outages = [cell for cell in outages if str(cell.get("area", "")).lower() == wanted]
    return {"area": area, "outages": outages}

################################################################################
def pay_bill(bill_id: str) -> dict:
    """Pay a bill. Pass customer_id and bill_id. The agent can only pay one bill at a time."""
    conn = connect()
    try:
        conn.execute("UPDATE bills SET paid = 1 WHERE id = ?", (bill_id,))
    finally:
        conn.close()
    return {"bill_id": bill_id, "paid": True}

################################################################################
def resume_line(line_id: str) -> dict:
    """Resume a suspended line after reading its current suspension_reason.

    fraud_hold is never cleared. non_payment is refused while any bill for that
    customer is still overdue. Otherwise status and suspension_reason change
    together.
    """
    conn = connect()
    try:
        line = conn.execute(
            "SELECT status, suspension_reason, customer_id FROM lines WHERE id = ?",
            (line_id,),
        ).fetchone()
        if line is None:
            return {"error": f"line {line_id} not found"}
        reason = line["suspension_reason"]
        if line["status"] == "active" and reason is None:
            return {"line_id": line_id, "status": "active", "suspension_reason": None}
        if line["status"] == "cancelled":
            return {"error": f"line {line_id} is cancelled and cannot be resumed"}
        if reason == "fraud_hold":
            return {"error": f"line {line_id} is on fraud_hold and cannot be resumed. Escalate to a human."}
        if reason == "non_payment":
            overdue = conn.execute(
                "SELECT id FROM bills WHERE customer_id = ? AND overdue = 1",
                (line["customer_id"],),
            ).fetchall()
            if overdue:
                ids = [row["id"] for row in overdue]
                return {"error": f"line {line_id} is suspended for non_payment. Pay overdue bills first: {ids}"}
        conn.execute(
            "UPDATE lines SET status = 'active', suspension_reason = NULL WHERE id = ?",
            (line_id,),
        )
        conn.commit()
    finally:
        conn.close()
    return {"line_id": line_id, "status": "active", "suspension_reason": None}

################################################################################
# Value from this instance's incident.close_code choices. State 6 is Resolved.
RESOLVED_CLOSE_CODE = "Resolved – Permanent Fix Applied"
RESOLVED_STATE = "6"


def _incident_query(incident_number: str) -> str:
    """ServiceNow query for the incident number the agent sees, or its correlation id."""
    incident_number = incident_number.strip()
    if incident_number.upper().startswith("INC"):
        return f"number={incident_number}"
    if incident_number.startswith("northwind-"):
        return f"correlation_id={incident_number}"
    return f"correlation_idENDSWITH{incident_number}"


def _ticket_id(correlation_id: str) -> str | None:
    """SQLite ticket id stored in correlation_id, for example northwind-seed-TKT-5001."""
    if not correlation_id or not correlation_id.startswith("northwind-"):
        return None
    ticket_id = correlation_id.split("-", 2)[-1]
    if not ticket_id.startswith("TKT-"):
        return None
    return ticket_id


def _find_incident(incident_number: str) -> dict:
    """Look up one incident. Returns session, url, and the row, or an error dict."""
    incident_number = (incident_number or "").strip()
    if not incident_number:
        return {"error": "pass incident_number"}
    load_env()
    try:
        base, auth = servicenow_config()
    except ConfigError as exc:
        return {"error": str(exc)}
    session = requests.Session()
    session.auth = auth
    session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
    url = f"{base}/api/now/table/incident"
    try:
        found = session.get(
            url,
            params={
                "sysparm_query": _incident_query(incident_number),
                "sysparm_fields": "sys_id,number,correlation_id,state",
                "sysparm_limit": 2,
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        return {"error": f"ServiceNow unreachable: {exc}"}
    if found.status_code != 200:
        return {"error": f"ServiceNow lookup failed ({found.status_code}): {found.text[:200]}"}
    rows = found.json().get("result") or []
    if not rows:
        return {"error": f"incident {incident_number} not found"}
    if len(rows) > 1:
        numbers = [row.get("number") for row in rows]
        return {"error": f"incident {incident_number} matched more than one record: {numbers}"}
    return {"session": session, "url": url, "incident": rows[0]}


def add_work_note(incident_number: str, note: str) -> dict:
    """Append one internal work note on the ServiceNow incident.

    work_notes is a journal field: the PATCH adds a note, it does not replace
    the old ones. The customer does not see work notes.
    """
    note = (note or "").strip()
    if not (incident_number or "").strip() or not note:
        return {"error": "pass incident_number and note"}
    found = _find_incident(incident_number)
    if "error" in found:
        return found
    incident = found["incident"]
    try:
        updated = found["session"].patch(
            f"{found['url']}/{incident['sys_id']}",
            json={"work_notes": note},
            timeout=30,
        )
    except requests.RequestException as exc:
        return {"error": f"ServiceNow unreachable: {exc}"}
    if updated.status_code != 200:
        return {"error": f"ServiceNow update failed ({updated.status_code}): {updated.text[:200]}"}
    return {"incident_number": incident.get("number"), "work_note": note}


def _resolve_sqlite_ticket(correlation_id: str, resolution: str) -> dict:
    """Copy the resolution onto the SQLite ticket the checker reads."""
    ticket_id = _ticket_id(correlation_id or "")
    if ticket_id is None:
        return {"sqlite_ticket": None}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = connect()
    try:
        row = conn.execute("SELECT id FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if row is None:
            return {"sqlite_ticket": None}
        conn.execute(
            "UPDATE tickets SET status = 'resolved', resolution = ?, updated_at = ? WHERE id = ?",
            (resolution, now, ticket_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"sqlite_ticket": ticket_id, "status": "resolved"}


def resolve_ticket(incident_number: str, resolution: str) -> dict:
    """Resolve the ServiceNow incident and the matching SQLite ticket.

    State 6 is Resolved. close_notes is the resolution text. The same text is
    written to tickets.resolution so check_end_state can see it.
    """
    resolution = (resolution or "").strip()
    if not (incident_number or "").strip() or not resolution:
        return {"error": "pass incident_number and resolution"}
    found = _find_incident(incident_number)
    if "error" in found:
        return found
    incident = found["incident"]
    if str(incident.get("state")) == RESOLVED_STATE:
        copied = _resolve_sqlite_ticket(incident.get("correlation_id") or "", resolution)
        return {"incident_number": incident.get("number"), "state": "resolved", "resolution": resolution, **copied}
    try:
        updated = found["session"].patch(
            f"{found['url']}/{incident['sys_id']}",
            json={
                "state": RESOLVED_STATE,
                "close_code": RESOLVED_CLOSE_CODE,
                "close_notes": resolution,
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        return {"error": f"ServiceNow unreachable: {exc}"}
    if updated.status_code != 200:
        return {"error": f"ServiceNow update failed ({updated.status_code}): {updated.text[:200]}"}
    copied = _resolve_sqlite_ticket(incident.get("correlation_id") or "", resolution)
    return {
        "incident_number": incident.get("number"),
        "state": "resolved",
        "resolution": resolution,
        **copied,
    }