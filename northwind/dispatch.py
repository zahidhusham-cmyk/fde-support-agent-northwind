"""Pull one open Northwind incident from ServiceNow and hand it to the agent.

    python -m northwind.dispatch
"""
from __future__ import annotations

import asyncio

import requests

from northwind.agent import load_ticket
from northwind.integrations import ConfigError, load_env, servicenow_config

# ServiceNow incident.state: 1 New. The seed sync creates incidents in this state.
NEW = "1"


def fetch_one_ticket() -> str:
    """Return the text of one New Northwind incident, oldest number first."""
    load_env()
    try:
        base, auth = servicenow_config()
    except ConfigError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    response = requests.get(
        f"{base}/api/now/table/incident",
        auth=auth,
        headers={"Accept": "application/json"},
        params={
            "sysparm_query": f"correlation_idSTARTSWITHnorthwind^state={NEW}^ORDERBYnumber",
            "sysparm_fields": "number,short_description,description,correlation_id",
            "sysparm_limit": 1,
        },
        timeout=30,
    )
    if response.status_code != 200:
        raise SystemExit(f"ERROR: ServiceNow lookup failed ({response.status_code}).")
    rows = response.json().get("result") or []
    if not rows:
        raise SystemExit("ERROR: no New Northwind incidents in ServiceNow.")
    incident = rows[0]
    return (
        f"Incident {incident.get('number')} ({incident.get('correlation_id')})\n"
        f"{incident.get('short_description')}\n\n"
        f"{incident.get('description')}"
    )


def main() -> None:
    ticket = fetch_one_ticket()
    print(f"Dispatching one ticket:\n{ticket}\n")
    asyncio.run(load_ticket(ticket))


if __name__ == "__main__":
    main()
