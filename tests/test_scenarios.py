import pytest

from northwind.scenarios import (OPS, check_end_state, list_scenarios, load_scenario, load_scenario_file)

SCENARIOS = list_scenarios()
REQUIRED_KEYS = {"id", "title", "difficulty", "root_causes", "customer_persona", "opening_message",
                 "initial_state", "expected_end_state", "notes"}


def apply_expected(conn, scenario):
    """Write values that satisfy expected_end_state, standing in for a perfect agent + user."""
    for table, rows in scenario["expected_end_state"].items():
        if table == "network":
            continue  # derived from the other tables; never written directly
        for pk, columns in rows.items():
            values = {}
            for column, expected in columns.items():
                if isinstance(expected, dict):
                    op, value = expected["op"], expected.get("value")
                    if op == "not_null":
                        value = "2026-11-25T12:00:00Z"
                    elif op == "is_null":
                        value = None
                    elif op == "gt":
                        value = (value or 0) + 1
                    elif op == "in":
                        value = value[0]
                    elif op == "contains":
                        value = f"Known {value} in the area, restore expected 9am."
                else:
                    value = expected
                values[column] = value
            # One UPDATE per row: CHECK constraints (e.g. suspended <=> reason) are row-level,
            # so related columns must change together, just like your tools will have to.
            sets = ", ".join(f"{c} = ?" for c in values)
            conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*values.values(), pk))
    conn.commit()


def test_there_are_about_ten_scenarios():
    assert len(SCENARIOS) >= 10


@pytest.mark.parametrize("sid", SCENARIOS)
def test_scenario_file_is_well_formed(sid):
    s = load_scenario_file(sid)
    assert REQUIRED_KEYS <= set(s)
    assert s["id"] == sid
    assert s["difficulty"] in {"easy", "medium", "hard"}
    assert s["root_causes"]
    assert "TKT-5001" in s["expected_end_state"]["tickets"]
    for cond in (c for rows in s["expected_end_state"].values() for cols in rows.values() for c in cols.values()):
        if isinstance(cond, dict):
            assert cond["op"] in OPS


@pytest.mark.parametrize("sid", SCENARIOS)
def test_scenario_loads_into_fresh_db(sid):
    conn = load_scenario(sid)
    for table, rows in load_scenario_file(sid)["initial_state"].items():
        if table == "cells":
            continue  # overrides of reference rows, not new rows
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == len(rows)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM cells").fetchone()[0] == 4


@pytest.mark.parametrize("sid", SCENARIOS)
def test_initial_state_fails_check(sid):
    """Nothing is solved before the agent starts."""
    result = check_end_state(load_scenario(sid), sid)
    assert not result.passed
    assert result.failures


@pytest.mark.parametrize("sid", SCENARIOS)
def test_expected_state_is_reachable_and_passes(sid):
    s = load_scenario_file(sid)
    conn = load_scenario(s)
    apply_expected(conn, s)  # also proves the end state satisfies the schema's CHECK constraints
    result = check_end_state(conn, s)
    assert result.passed, str(result)


def test_each_load_is_isolated():
    a = load_scenario("s01_airplane_mode")
    a.execute("UPDATE devices SET airplane_mode = 0")
    b = load_scenario("s01_airplane_mode")
    assert b.execute("SELECT airplane_mode FROM devices").fetchone()[0] == 1


def test_load_to_file(tmp_path):
    path = tmp_path / "run.db"
    load_scenario("s02_mobile_data_off", path).close()
    load_scenario("s02_mobile_data_off", path).close()  # reloading replaces the file
    from northwind.db import connect
    assert connect(path).execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 1


def test_partial_fix_reports_remaining_failures():
    s = load_scenario_file("s08_airplane_and_data_off")
    conn = load_scenario(s)
    conn.execute("UPDATE devices SET airplane_mode = 0")
    conn.execute("UPDATE tickets SET status = 'resolved'")
    result = check_end_state(conn, s)
    assert not result.passed
    assert result.failures == [
        "devices.DEV-4001.mobile_data_enabled: expected == 1, got 0",
        "network.LIN-2001.data_session_active: expected == True, got False",
    ]


def test_fraud_hold_trap_resuming_the_line_fails():
    s = load_scenario_file("s12_fraud_hold")
    conn = load_scenario(s)
    conn.execute("UPDATE lines SET status = 'active', suspension_reason = NULL")
    conn.execute("UPDATE tickets SET status = 'escalated'")
    result = check_end_state(conn, s)
    assert not result.passed
    assert any("lines.LIN-2001.status" in f for f in result.failures)


def test_hardware_fault_marked_resolved_fails():
    s = load_scenario_file("s11_hardware_fault")
    conn = load_scenario(s)
    conn.execute("UPDATE tickets SET status = 'resolved'")
    assert not check_end_state(conn, s).passed


def test_missing_row_is_a_failure():
    s = load_scenario_file("s01_airplane_mode")
    conn = load_scenario(s)
    conn.execute("DELETE FROM tickets")
    assert "tickets.TKT-5001: row missing" in check_end_state(conn, s).failures


def test_operator_gte_on_data_topup():
    s = load_scenario_file("s04_data_cap_reached")
    conn = load_scenario(s)
    conn.execute("UPDATE tickets SET status = 'resolved'")
    conn.execute("UPDATE lines SET data_topup_mb = 512")
    assert not check_end_state(conn, s).passed
    conn.execute("UPDATE lines SET data_topup_mb = 2048")
    assert check_end_state(conn, s).passed


def test_unknown_operator_raises():
    s = load_scenario_file("s01_airplane_mode")
    s["expected_end_state"]["devices"]["DEV-4001"]["airplane_mode"] = {"op": "approx", "value": 0}
    with pytest.raises(ValueError):
        check_end_state(load_scenario(s), s)
