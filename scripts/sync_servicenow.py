"""Push tickets from a Northwind SQLite DB into ServiceNow incidents on your PDI. Safe to re-run.

    python scripts/sync_servicenow.py                         # seed DB (northwind.db), tag "seed"
    python scripts/sync_servicenow.py --db run.db --tag s03   # a scenario DB you loaded
    python scripts/sync_servicenow.py --dry-run               # print payloads, call nothing

Each tickets row becomes one record in the `incident` table via the Table API:
  POST  /api/now/table/incident                       create
  GET   /api/now/table/incident?sysparm_query=correlation_id=<id>   find existing (idempotency)

correlation_id = "northwind-<tag>-<ticket id>", e.g. "northwind-seed-TKT-5001".
The customer and line are written into the description (we don't create sys_user callers).
Only open / in_progress tickets are synced; resolving and escalating is your agent's job.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from northwind.db import DEFAULT_DB_PATH, connect  # noqa: E402
from northwind.integrations import ConfigError, fail, load_env, servicenow_config  # noqa: E402

# ServiceNow incident.state choice values (out of the box): 1 New, 2 In Progress.
STATE = {"open": "1", "in_progress": "2"}


def correlation_id(tag: str, ticket_id: str) -> str:
    return f"northwind-{tag}-{ticket_id}"


def build_payloads(conn, tag: str) -> list[dict]:
    """Pure function: incident bodies derived from SQLite. No network."""
    rows = conn.execute(
        "SELECT t.*, c.full_name, c.email, l.msisdn FROM tickets t "
        "JOIN customers c ON c.id = t.customer_id LEFT JOIN lines l ON l.id = t.line_id ORDER BY t.id").fetchall()
    payloads = []
    for t in rows:
        if t["status"] not in STATE:
            continue
        payloads.append({
            "short_description": t["subject"],
            "description": (f"{t['description']}\n\nCustomer: {t['full_name']} <{t['email']}> ({t['customer_id']})\n"
                            f"Line: {t['msisdn']} ({t['line_id']})"),
            "state": STATE[t["status"]],
            "category": "network",
            "correlation_id": correlation_id(tag, t["id"]),
            "correlation_display": "Northwind Mobile",
        })
    return payloads


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Northwind tickets to ServiceNow incidents.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--tag", default="seed")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not Path(args.db).exists():
        fail(f"{args.db} not found. Run python -m northwind.seed (or load a scenario) first.")
    payloads = build_payloads(connect(args.db), args.tag)
    if args.dry_run:
        for p in payloads:
            print(f"{p['correlation_id']}: {p['short_description']}")
        print(f"Dry run: {len(payloads)} incidents. Nothing sent.")
        return

    load_env()
    try:
        base, auth = servicenow_config()
    except ConfigError as e:
        fail(str(e))
    import requests
    session = requests.Session()
    session.auth = auth
    session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
    url = f"{base}/api/now/table/incident"
    created = existing = 0
    for p in payloads:
        r = session.get(url, params={"sysparm_query": f"correlation_id={p['correlation_id']}",
                                     "sysparm_fields": "sys_id,number", "sysparm_limit": 1}, timeout=30)
        if r.status_code != 200:
            fail(f"Lookup failed ({r.status_code}). Run scripts/check_servicenow.py first.")
        found = r.json().get("result", [])
        if found:
            existing += 1
            print(f"  {p['correlation_id']} already exists as {found[0]['number']}")
            continue
        r = session.post(url, json=p, timeout=30)
        if r.status_code != 201:
            fail(f"Create failed ({r.status_code}): {r.text[:300]}")
        created += 1
        print(f"  {p['correlation_id']} -> {r.json()['result']['number']}")
    print(f"Done: created={created}, existing={existing}")


if __name__ == "__main__":
    main()
