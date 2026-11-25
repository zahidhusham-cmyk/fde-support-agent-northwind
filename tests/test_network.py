import pytest

from northwind.network import data_works, derive_network_status, get_network_status, mark_last_seen
from northwind.scenarios import check_end_state, load_scenario, load_scenario_file

NOW = "2026-11-25T13:00:00Z"
SYMPTOM_KEYS = ["registration_state", "registered", "reject_reason", "rat", "signal_bars", "rsrp_dbm",
                "serving_cell_id", "roaming_network", "data_session_active", "data_session_error", "data_policy_block"]


def healthy():
    return load_scenario("s02_mobile_data_off")  # healthy except mobile data off; we reset it below


def net(conn, **device_changes):
    conn.execute("UPDATE devices SET mobile_data_enabled = 1")
    for k, v in device_changes.items():
        conn.execute(f"UPDATE devices SET {k} = ?", (v,))
    return get_network_status(conn, "LIN-2001", now=NOW)


def test_healthy_line_is_registered_with_data():
    n = net(healthy())
    assert n["registration_state"] == "registered"
    assert n["rat"] == "5G" and n["serving_cell_id"] == "CELL-MAN-0412"
    assert n["signal_bars"] == 4 and n["rsrp_dbm"] == -90
    assert n["data_session_active"] is True and n["last_seen"] == NOW


@pytest.mark.parametrize("cause", [
    {"airplane_mode": 1}, {"powered_on": 0}, {"sim_ok": 0}, {"needs_restart": 1}, {"hardware_fault": 1},
    {"signal_bars": 0},
])
def test_device_side_causes_are_indistinguishable_from_the_network(cause):
    """The core realism rule: symptoms, not causes."""
    reference = net(healthy(), airplane_mode=1)
    n = net(healthy(), **cause)
    assert {k: n[k] for k in SYMPTOM_KEYS} == {k: reference[k] for k in SYMPTOM_KEYS}
    assert n["registration_state"] == "not_registered" and n["rat"] == "none"


def test_not_registered_shows_last_seen_from_device_history():
    conn = load_scenario("s01_airplane_mode")
    n = get_network_status(conn, "LIN-2001", now=NOW)
    assert n["registration_state"] == "not_registered"
    assert n["last_seen"] == "2026-11-25T06:05:00Z"


def test_mobile_data_off_looks_like_no_traffic_not_an_error():
    n = get_network_status(load_scenario("s02_mobile_data_off"), "LIN-2001", now=NOW)
    assert n["registered"] and not n["data_session_active"]
    assert n["data_session_error"] is None and n["data_policy_block"] is None


@pytest.mark.parametrize("sid,field,value", [
    ("s03_suspended_overdue_bill", "reject_reason", "line_suspended"),
    ("s12_fraud_hold", "reject_reason", "line_suspended"),
    ("s07_roaming_abroad", "reject_reason", "roaming_not_allowed"),
    ("s04_data_cap_reached", "data_policy_block", "cap_reached"),
    ("s05_bad_apn_provisioning", "data_session_error", "unknown_apn"),
])
def test_carrier_side_causes_are_attributed(sid, field, value):
    assert get_network_status(load_scenario(sid), "LIN-2001", now=NOW)[field] == value


def test_suspended_line_is_rejected_but_seen_now():
    n = get_network_status(load_scenario("s03_suspended_overdue_bill"), "LIN-2001", now=NOW)
    assert n["registration_state"] == "rejected" and n["last_seen"] == NOW


def test_outage_cell_means_not_registered_and_cell_shows_outage():
    conn = load_scenario("s13_tower_outage")
    n = get_network_status(conn, "LIN-2001", now=NOW)
    assert n["registration_state"] == "not_registered" and n["last_seen"] == "2026-11-25T05:40:00Z"
    assert n["serving_cell_id"] is None and n["last_cell_id"] == "CELL-QNS-0107"  # how the agent finds the outage
    cell = conn.execute("SELECT status, estimated_restore_at FROM cells WHERE id = 'CELL-QNS-0107'").fetchone()
    assert tuple(cell) == ("outage", "2026-11-25T14:00:00Z")
    # Nothing on the phone helps:
    conn.execute("UPDATE devices SET needs_restart = 0, airplane_mode = 0")
    assert not data_works(conn, "LIN-2001")
    # Outage ends -> service returns with no phone changes
    conn.execute("UPDATE cells SET status = 'up', outage_reason = NULL WHERE id = 'CELL-QNS-0107'")
    assert data_works(conn, "LIN-2001")


def test_degraded_cell_falls_back_to_lte_with_weak_signal():
    conn = healthy()
    conn.execute("UPDATE cells SET status = 'degraded', outage_reason = 'backhaul congestion' WHERE id = 'CELL-MAN-0412'")
    n = net(conn)
    assert n["registered"] and n["rat"] == "LTE" and n["signal_bars"] == 2 and n["data_session_active"]


def test_roaming_needs_both_carrier_permission_and_phone_toggle():
    conn = load_scenario("s07_roaming_abroad")
    conn.execute("UPDATE lines SET roaming_enabled = 1")
    n = get_network_status(conn, "LIN-2001", now=NOW)
    assert n["registered"] and n["roaming_network"] == "Rogers Wireless (CA)" and n["serving_cell_id"] is None
    assert not n["data_session_active"]  # phone's data roaming still off
    conn.execute("UPDATE devices SET data_roaming_on = 1")
    assert data_works(conn, "LIN-2001")


def test_topup_clears_cap_block():
    conn = load_scenario("s04_data_cap_reached")
    conn.execute("UPDATE lines SET data_topup_mb = 1024")
    n = get_network_status(conn, "LIN-2001", now=NOW)
    assert n["data_policy_block"] is None and n["data_session_active"] and n["data_allowance_mb"] == 5120 + 1024


def test_view_stays_consistent_when_customer_changes_phone():
    conn = load_scenario("s01_airplane_mode")
    assert not data_works(conn, "LIN-2001")
    conn.execute("UPDATE devices SET airplane_mode = 0")
    assert data_works(conn, "LIN-2001")


def test_mark_last_seen_only_touches_reachable_devices():
    conn = load_scenario("s01_airplane_mode")  # unreachable
    mark_last_seen(conn, now=NOW)
    assert conn.execute("SELECT last_attached_at FROM devices").fetchone()[0] == "2026-11-25T06:05:00Z"
    conn.execute("UPDATE devices SET airplane_mode = 0")
    mark_last_seen(conn, now=NOW)
    assert conn.execute("SELECT last_attached_at FROM devices").fetchone()[0] == NOW


def test_unknown_line_raises():
    with pytest.raises(KeyError):
        get_network_status(healthy(), "LIN-9999")


def test_derive_is_pure_on_plain_dicts():
    line = dict(id="L", status="active", roaming_enabled=0, data_used_mb=0, data_topup_mb=0, provisioning_ok=1)
    device = dict(powered_on=1, airplane_mode=0, sim_ok=1, needs_restart=0, hardware_fault=0, signal_bars=5,
                  is_abroad=0, mobile_data_enabled=1, data_roaming_on=0, apn_ok=1, cell_id="C", visited_network=None,
                  last_attached_at=None)
    n = derive_network_status(line, device, {"data_cap_mb": 100}, {"status": "up", "rat": "LTE"}, now=NOW)
    assert n["data_session_active"] and n["rsrp_dbm"] == -80 and n["rat"] == "LTE"


# --- check_end_state with the network pseudo-table and new operators -------------------------

def test_check_end_state_network_missing_line():
    s = load_scenario_file("s01_airplane_mode")
    s["expected_end_state"]["network"] = {"LIN-9999": {"registered": True}}
    assert "network.LIN-9999: row missing" in check_end_state(load_scenario(s), s).failures


def test_check_end_state_unknown_network_field_raises():
    s = load_scenario_file("s01_airplane_mode")
    s["expected_end_state"]["network"] = {"LIN-2001": {"cause": "airplane"}}
    with pytest.raises(ValueError):
        check_end_state(load_scenario(s), s)


def test_tower_outage_grading():
    s = load_scenario_file("s13_tower_outage")
    conn = load_scenario(s)
    # Escalating needlessly fails
    conn.execute("UPDATE tickets SET status = 'escalated', resolution = 'Escalated to field team: outage?'")
    assert not check_end_state(conn, s).passed
    # Resolving without explaining the outage fails
    conn.execute("UPDATE tickets SET status = 'resolved', resolution = 'Customer told to wait'")
    assert not check_end_state(conn, s).passed
    # Leaving the customer in airplane mode after pointless troubleshooting fails
    conn.execute("UPDATE tickets SET resolution = 'Known OUTAGE on Astoria tower, ETA 9am'")
    conn.execute("UPDATE devices SET airplane_mode = 1")
    assert not check_end_state(conn, s).passed
    conn.execute("UPDATE devices SET airplane_mode = 0")
    assert check_end_state(conn, s).passed


def test_sim_scenario_needs_the_sim_fixed_not_airplane_mode():
    s = load_scenario_file("s14_sim_unseated")
    conn = load_scenario(s)
    conn.execute("UPDATE tickets SET status = 'resolved'")
    conn.execute("UPDATE devices SET airplane_mode = 0, needs_restart = 0")
    result = check_end_state(conn, s)
    assert not result.passed and any("sim_ok" in f for f in result.failures)
