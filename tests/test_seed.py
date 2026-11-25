import sqlite3

import pytest

from northwind.db import fresh_db, insert_rows
from northwind.seed import seed


@pytest.fixture
def conn(tmp_path):
    c = seed(tmp_path / "test.db")
    yield c
    c.close()


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_seed_row_counts(conn):
    assert count(conn, "plans") == 3
    assert count(conn, "customers") == 12
    assert count(conn, "lines") == 12
    assert count(conn, "devices") == 12
    assert count(conn, "bills") == 36
    assert count(conn, "tickets") == 9
    assert count(conn, "cells") == 4


def test_seed_foreign_keys_are_valid(conn):
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_seed_is_deterministic(tmp_path):
    a = seed(tmp_path / "a.db")
    b = seed(tmp_path / "b.db")
    for table in ["customers", "lines", "bills", "devices", "tickets"]:
        q = f"SELECT * FROM {table} ORDER BY id"
        assert [tuple(r) for r in a.execute(q)] == [tuple(r) for r in b.execute(q)]


def test_seed_rerun_replaces_existing_file(tmp_path):
    path = tmp_path / "x.db"
    seed(path).close()
    c = seed(path)
    assert count(c, "customers") == 12


def test_seed_contains_problem_customers(conn):
    # Liam: suspended for non-payment with an overdue bill
    liam = conn.execute(
        "SELECT l.status, l.suspension_reason FROM lines l JOIN customers c ON c.id = l.customer_id "
        "WHERE c.full_name = 'Liam O''Connor'").fetchone()
    assert tuple(liam) == ("suspended", "non_payment")
    overdue = conn.execute(
        "SELECT COUNT(*) FROM bills b JOIN customers c ON c.id = b.customer_id "
        "WHERE c.full_name = 'Liam O''Connor' AND b.overdue = 1").fetchone()[0]
    assert overdue == 1
    # Daniel: airplane mode on
    assert conn.execute(
        "SELECT d.airplane_mode FROM devices d JOIN lines l ON l.id = d.line_id "
        "JOIN customers c ON c.id = l.customer_id WHERE c.full_name = 'Daniel Okafor'").fetchone()[0] == 1


def test_every_open_ticket_points_at_the_customers_own_line(conn):
    bad = conn.execute(
        "SELECT t.id FROM tickets t JOIN lines l ON l.id = t.line_id WHERE l.customer_id != t.customer_id").fetchall()
    assert bad == []


@pytest.mark.parametrize("bad_line", [
    {"status": "suspended", "suspension_reason": None},     # suspended needs a reason
    {"status": "active", "suspension_reason": "fraud_hold"},  # active must not have one
    {"status": "paused", "suspension_reason": None},        # unknown status
])
def test_schema_rejects_inconsistent_lines(bad_line):
    c = fresh_db()
    insert_rows(c, "customers", [dict(id="CUS-1", full_name="A", email="a@example.com", phone_contact="1",
                                      date_of_birth="2000-01-01", account_pin="0000", created_at="2026-01-01")])
    with pytest.raises(sqlite3.IntegrityError):
        insert_rows(c, "lines", [dict(id="LIN-1", customer_id="CUS-1", msisdn="+1", plan_id="PLN-BASIC", **bad_line)])


def test_schema_rejects_paid_and_overdue_bill():
    c = fresh_db()
    insert_rows(c, "customers", [dict(id="CUS-1", full_name="A", email="a@example.com", phone_contact="1",
                                      date_of_birth="2000-01-01", account_pin="0000", created_at="2026-01-01")])
    with pytest.raises(sqlite3.IntegrityError):
        insert_rows(c, "bills", [dict(id="B-1", customer_id="CUS-1", period="2026-11", amount_cents=100,
                                      due_date="2026-11-28", paid_at="2026-11-01", overdue=1)])
