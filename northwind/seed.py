"""Seed a demo database (northwind.db) with realistic Northwind Mobile data.

    python -m northwind.seed                 # writes ./northwind.db
    python -m northwind.seed --db /tmp/x.db  # somewhere else

The data is deterministic (no randomness) so everyone gets the same rows. It mixes
healthy customers with ones who have the problems the scenarios cover, so you can
poke at it with SQL before writing any tools.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from northwind.db import DEFAULT_DB_PATH, fresh_db, insert_rows

PERIODS = [("2026-09", "2026-09-28"), ("2026-10", "2026-10-28"), ("2026-11", "2026-11-28")]
PLAN_PRICE = {"PLN-BASIC": 2500, "PLN-PLUS": 4000, "PLN-UNLTD": 6500}

# One entry per customer. Device/line keys override the healthy defaults below.
# `unpaid` = billing periods left unpaid; `overdue` = which of those are past due.
CUSTOMERS = [
    dict(name="Maya Patel", dob="1991-04-12", pin="4821", plan="PLN-PLUS", device={"model": "Pixel 8", "os": "android"},
         ticket=None),
    dict(name="Daniel Okafor", dob="1985-11-03", pin="1937", plan="PLN-UNLTD", device={"model": "iPhone 15", "os": "ios", "airplane_mode": 1, "last_attached_at": "2026-11-25T06:48:00Z"},
         ticket=("No data since this morning", "Phone says no internet. I haven't changed anything.")),
    dict(name="Sofia Hernandez", dob="1998-07-22", pin="5560", plan="PLN-BASIC", device={"model": "Galaxy A54", "os": "android", "mobile_data_enabled": 0, "cell_id": "CELL-BKN-0219"},
         ticket=("Internet stopped working", "Wi-Fi works at home but nothing when I leave the house.")),
    dict(name="Liam O'Connor", dob="1979-02-17", pin="7302", plan="PLN-PLUS", line={"status": "suspended", "suspension_reason": "non_payment"},
         unpaid=["2026-10", "2026-11"], overdue=["2026-10"], device={"model": "iPhone 13", "os": "ios", "cell_id": "CELL-JC-0033"},
         ticket=("Data and calls not working", "Can't load anything or make calls. Is the network down?")),
    dict(name="Aisha Rahman", dob="2001-09-30", pin="2468", plan="PLN-BASIC", line={"data_used_mb": 5_120}, device={"model": "Moto G Power", "os": "android"},
         ticket=("Data super slow then stopped", "It worked fine at the start of the month, now nothing loads.")),
    dict(name="Ethan Brooks", dob="1993-12-08", pin="8015", plan="PLN-PLUS", line={"provisioning_ok": 0}, device={"model": "Pixel 7a", "os": "android", "apn_ok": 0},
         ticket=("New SIM, no data", "Got a replacement SIM yesterday. Calls work, data doesn't.")),
    dict(name="Grace Kim", dob="1988-05-25", pin="3391", plan="PLN-UNLTD", device={"model": "iPhone 14 Pro", "os": "ios", "needs_restart": 1, "last_attached_at": "2026-11-25T03:05:00Z"},
         ticket=("No signal after update", "Phone updated overnight and now it shows no bars.")),
    dict(name="Marcus Johnson", dob="1975-08-14", pin="6604", plan="PLN-PLUS", device={"model": "Galaxy S23", "os": "android", "is_abroad": 1, "cell_id": None, "visited_network": "Rogers Wireless (CA)"},
         ticket=("No data in Toronto", "Travelling for work, data hasn't worked since I landed.")),
    dict(name="Priya Nair", dob="1996-01-19", pin="9172", plan="PLN-BASIC", device={"model": "iPhone SE", "os": "ios", "cell_id": "CELL-QNS-0107", "last_attached_at": "2026-11-25T05:40:00Z"},
         unpaid=["2026-11"], overdue=[], ticket=None),
    dict(name="Tomás Silva", dob="1983-06-02", pin="1150", plan="PLN-UNLTD", device={"model": "Pixel 8 Pro", "os": "android", "hardware_fault": 1, "last_attached_at": "2026-11-19T22:10:00Z"},
         ticket=("Phone dropped in water", "Dropped it in the sink, dried it off, now there's never any signal.")),
    dict(name="Hannah Weber", dob="1990-10-27", pin="4479", plan="PLN-PLUS", device={"model": "Galaxy Z Flip5", "os": "android", "cell_id": "CELL-QNS-0107", "last_attached_at": "2026-11-25T05:40:00Z"},
         ticket=("No service in Astoria", "Nothing since about 1am. Restarted twice. My neighbour has no signal either.")),
    dict(name="Kenji Watanabe", dob="1987-03-11", pin="2803", plan="PLN-BASIC", line={"status": "suspended", "suspension_reason": "customer_request"},
         device={"model": "iPhone 12", "os": "ios", "cell_id": "CELL-BKN-0219"}, ticket=None),
]

DEVICE_DEFAULTS = dict(airplane_mode=0, mobile_data_enabled=1, data_roaming_on=0, is_abroad=0, apn_ok=1,
                       needs_restart=0, hardware_fault=0, signal_bars=4, powered_on=1, sim_ok=1,
                       cell_id="CELL-MAN-0412", visited_network=None, last_attached_at=None)

# Live network events in the demo DB: one tower is down (affects the two Astoria customers).
CELL_OVERRIDES = [
    {"id": "CELL-QNS-0107", "name": "Astoria Ditmars Blvd", "area": "Queens", "rat": "5G", "status": "outage",
     "outage_reason": "fiber cut", "outage_started_at": "2026-11-25T05:40:00Z",
     "estimated_restore_at": "2026-11-25T14:00:00Z"},
]
LINE_DEFAULTS = dict(status="active", suspension_reason=None, data_used_mb=1_200, data_topup_mb=0,
                     provisioning_ok=1, roaming_enabled=0)


def build_rows() -> dict[str, list[dict]]:
    rows: dict[str, list[dict]] = {"customers": [], "lines": [], "bills": [], "devices": [], "tickets": []}
    bill_n = ticket_n = 0
    for i, c in enumerate(CUSTOMERS):
        cus_id, lin_id, dev_id = f"CUS-{1001 + i}", f"LIN-{2001 + i}", f"DEV-{4001 + i}"
        first, last = c["name"].split(" ", 1)
        email = f"{first}.{last}".lower().replace("'", "").replace(" ", "").replace("á", "a") + "@example.com"
        rows["customers"].append(dict(
            id=cus_id, full_name=c["name"], email=email, phone_contact=f"+1-555-01{i:02d}",
            date_of_birth=c["dob"], account_pin=c["pin"], created_at=f"2024-{(i % 12) + 1:02d}-15T10:00:00Z"))
        rows["lines"].append(dict(
            LINE_DEFAULTS, id=lin_id, customer_id=cus_id, msisdn=f"+1-212-555-{2001 + i:04d}", plan_id=c["plan"],
            **c.get("line", {})))
        rows["devices"].append(dict(DEVICE_DEFAULTS, id=dev_id, line_id=lin_id, **c["device"]))
        for period, due in PERIODS:
            bill_n += 1
            unpaid = period in c.get("unpaid", [])
            rows["bills"].append(dict(
                id=f"BIL-{3000 + bill_n}", customer_id=cus_id, period=period, amount_cents=PLAN_PRICE[c["plan"]],
                due_date=due, paid_at=None if unpaid else f"{due}T09:00:00Z",
                overdue=1 if period in c.get("overdue", []) else 0))
        if c["ticket"]:
            ticket_n += 1
            subject, desc = c["ticket"]
            rows["tickets"].append(dict(
                id=f"TKT-{5000 + ticket_n}", customer_id=cus_id, line_id=lin_id, subject=subject, description=desc,
                status="open", resolution=None, created_at="2026-11-25T08:00:00Z", updated_at="2026-11-25T08:00:00Z"))
    return rows


def seed(db_path: str | Path = DEFAULT_DB_PATH):
    conn = fresh_db(db_path)
    insert_rows(conn, "cells", CELL_OVERRIDES, replace=True)
    for table, table_rows in build_rows().items():
        insert_rows(conn, table, table_rows)
    conn.commit()
    return conn


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite file to (re)create")
    args = parser.parse_args()
    conn = seed(args.db)
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ["plans", "cells", "customers", "lines", "bills", "devices", "tickets"]}
    conn.close()
    print(f"Seeded {args.db}")
    for table, n in counts.items():
        print(f"  {table:<10} {n}")


if __name__ == "__main__":
    main()
