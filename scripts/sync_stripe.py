"""Push customers and bills from a Northwind SQLite DB into Stripe TEST mode. Safe to re-run.

    python scripts/sync_stripe.py                          # seed DB (northwind.db), tag "seed"
    python scripts/sync_stripe.py --db run.db --tag s03    # a scenario DB you loaded
    python scripts/sync_stripe.py --dry-run                # print the plan, call nothing

What it creates, per SQLite row:
  customers row -> Stripe Customer   metadata: northwind_id, northwind_tag
  bills row     -> Stripe Invoice    collection_method=send_invoice, one line item for the amount,
                                     metadata: northwind_bill_id, northwind_tag, period, due_date, overdue
                   paid bills   -> finalized, then marked paid out of band (no charge, no card needed)
                   unpaid bills -> finalized and left open

Overdue: Stripe won't accept a due date in the past, so the real SQLite due date and the overdue
flag are stored in invoice metadata (northwind_due_date, northwind_overdue="1").

Idempotency: customers are found by email + northwind_tag metadata, invoices by northwind_bill_id
metadata on that customer. Existing objects are left alone, so re-running creates nothing new.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from northwind.db import DEFAULT_DB_PATH, connect  # noqa: E402
from northwind.integrations import ConfigError, fail, load_env, stripe_test_key  # noqa: E402


def build_plan(conn, tag: str) -> list[dict]:
    """Pure function: what we want in Stripe, derived from SQLite. No network."""
    plan = []
    for c in conn.execute("SELECT * FROM customers ORDER BY id"):
        bills = conn.execute("SELECT * FROM bills WHERE customer_id = ? ORDER BY period", (c["id"],)).fetchall()
        plan.append({
            "customer": {
                "email": c["email"], "name": c["full_name"], "phone": c["phone_contact"],
                "metadata": {"northwind_id": c["id"], "northwind_tag": tag},
            },
            "invoices": [{
                "amount": b["amount_cents"], "currency": "usd",
                "description": f"Northwind Mobile service {b['period']}",
                "paid": b["paid_at"] is not None,
                "metadata": {"northwind_bill_id": b["id"], "northwind_tag": tag, "northwind_period": b["period"],
                             "northwind_due_date": b["due_date"], "northwind_overdue": str(b["overdue"])},
            } for b in bills],
        })
    return plan


def find_customer(stripe, spec: dict):
    for cust in stripe.Customer.list(email=spec["email"], limit=100).auto_paging_iter():
        if cust.metadata.get("northwind_tag") == spec["metadata"]["northwind_tag"]:
            return cust
    return None


def find_invoice(stripe, customer_id: str, bill_id: str):
    for inv in stripe.Invoice.list(customer=customer_id, limit=100).auto_paging_iter():
        if inv.metadata.get("northwind_bill_id") == bill_id:
            return inv
    return None


def sync(stripe, plan: list[dict]) -> dict:
    counts = {"customers_created": 0, "customers_existing": 0, "invoices_created": 0, "invoices_existing": 0}
    for entry in plan:
        spec = entry["customer"]
        cust = find_customer(stripe, spec)
        if cust:
            counts["customers_existing"] += 1
        else:
            cust = stripe.Customer.create(**spec)
            counts["customers_created"] += 1
        for inv_spec in entry["invoices"]:
            bill_id = inv_spec["metadata"]["northwind_bill_id"]
            if find_invoice(stripe, cust.id, bill_id):
                counts["invoices_existing"] += 1
                continue
            inv = stripe.Invoice.create(customer=cust.id, collection_method="send_invoice", days_until_due=30,
                                        auto_advance=False, metadata=inv_spec["metadata"],
                                        description=inv_spec["description"])
            stripe.InvoiceItem.create(customer=cust.id, invoice=inv.id, amount=inv_spec["amount"],
                                      currency=inv_spec["currency"], description=inv_spec["description"])
            stripe.Invoice.finalize_invoice(inv.id, auto_advance=False)
            if inv_spec["paid"]:
                stripe.Invoice.pay(inv.id, paid_out_of_band=True)
            counts["invoices_created"] += 1
            print(f"  {spec['metadata']['northwind_id']} {bill_id} -> {inv.id} ({'paid' if inv_spec['paid'] else 'open'})")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Northwind customers/bills to Stripe test mode.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--tag", default="seed", help="label stored in metadata so seed and scenario data don't mix")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not Path(args.db).exists():
        fail(f"{args.db} not found. Run python -m northwind.seed (or load a scenario) first.")
    plan = build_plan(connect(args.db), args.tag)
    n_inv = sum(len(e["invoices"]) for e in plan)
    if args.dry_run:
        for e in plan:
            c = e["customer"]
            print(f"{c['metadata']['northwind_id']} {c['email']}: "
                  + ", ".join(f"{i['metadata']['northwind_bill_id']}({'paid' if i['paid'] else 'open'})" for i in e["invoices"]))
        print(f"Dry run: {len(plan)} customers, {n_inv} invoices. Nothing sent.")
        return

    load_env()
    try:
        key = stripe_test_key()
    except ConfigError as e:
        fail(str(e))
    import stripe
    stripe.api_key = key
    try:
        counts = sync(stripe, plan)
    except stripe.StripeError as e:
        fail(f"Stripe call failed: {e.user_message or e}")
    print("Done:", ", ".join(f"{k}={v}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
