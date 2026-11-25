import pytest

fastapi = pytest.importorskip("fastapi")  # optional deps: pip install -r requirements-integrations.txt
from fastapi.testclient import TestClient  # noqa: E402

from mock_network.app import app  # noqa: E402
from northwind.scenarios import check_end_state, load_scenario  # noqa: E402
from northwind.seed import seed  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "net.db"
    seed(path).close()
    monkeypatch.setenv("NORTHWIND_DB", str(path))
    return TestClient(app)


def scenario_client(tmp_path, monkeypatch, sid):
    path = tmp_path / f"{sid}.db"
    conn = load_scenario(sid, path)
    monkeypatch.setenv("NORTHWIND_DB", str(path))
    return TestClient(app), conn


def test_health(client):
    assert client.get("/health").json()["ok"] is True


def test_get_line(client):
    r = client.get("/lines/LIN-2004")  # Liam, suspended for non-payment
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "suspended" and body["suspension_reason"] == "non_payment"
    assert body["provisioning_ok"] is True


def test_find_line_by_msisdn(client):
    r = client.get("/lines", params={"msisdn": "+1-212-555-2002"})
    assert [l["id"] for l in r.json()] == ["LIN-2002"]


def test_find_lines_needs_a_filter(client):
    assert client.get("/lines").status_code == 422


def test_unknown_line_is_404(client):
    assert client.get("/lines/LIN-9999").status_code == 404


def test_patch_line_resume(client):
    r = client.patch("/lines/LIN-2004", json={"status": "active", "suspension_reason": None})
    assert r.status_code == 200
    assert r.json()["status"] == "active" and r.json()["suspension_reason"] is None
    assert client.get("/lines/LIN-2004").json()["status"] == "active"


def test_patch_line_inconsistent_state_is_409(client):
    # active but still carrying a suspension reason violates the schema CHECK
    r = client.patch("/lines/LIN-2004", json={"status": "active"})
    assert r.status_code == 409
    assert client.get("/lines/LIN-2004").json()["status"] == "suspended"


def test_patch_line_rejects_unknown_fields_and_bad_values(client):
    assert client.patch("/lines/LIN-2001", json={"balance": 0}).status_code == 422
    assert client.patch("/lines/LIN-2001", json={"status": "paused"}).status_code == 422
    assert client.patch("/lines/LIN-2001", json={"data_topup_mb": -5}).status_code == 422
    assert client.patch("/lines/LIN-2001", json={}).status_code == 422


def test_patch_line_unknown_plan_is_409(client):
    assert client.patch("/lines/LIN-2001", json={"plan_id": "PLN-NOPE"}).status_code == 409


def test_device_read_and_patch(client):
    r = client.get("/lines/LIN-2002/device")  # Daniel, airplane mode on
    assert r.status_code == 200 and r.json()["airplane_mode"] is True
    dev_id = r.json()["id"]
    r = client.patch(f"/devices/{dev_id}", json={"airplane_mode": False}, headers={"X-Actor": "user"})
    assert r.status_code == 200 and r.json()["airplane_mode"] is False


def test_device_signal_bars_range(client):
    assert client.patch("/devices/DEV-4001", json={"signal_bars": 9}).status_code == 422


@pytest.mark.parametrize("method,path", [
    ("get", "/devices/DEV-4001"), ("get", "/lines/LIN-2001/device"), ("patch", "/devices/DEV-4001")])
def test_agent_cannot_touch_devices(client, method, path):
    kwargs = {"headers": {"X-Actor": "agent"}}
    if method == "patch":
        kwargs["json"] = {"airplane_mode": False}
    assert getattr(client, method)(path, **kwargs).status_code == 403


def test_agent_can_read_lines(client):
    assert client.get("/lines/LIN-2001", headers={"X-Actor": "agent"}).status_code == 200


def test_missing_db_is_503(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHWIND_DB", str(tmp_path / "nope.db"))
    assert TestClient(app).get("/lines/LIN-2001").status_code == 503


def test_api_changes_count_toward_scenario_check(tmp_path, monkeypatch):
    """Fix s08 entirely through the API, then the scenario checker sees it."""
    client, conn = scenario_client(tmp_path, monkeypatch, "s08_airplane_and_data_off")
    client.patch("/devices/DEV-4001", json={"airplane_mode": False, "mobile_data_enabled": True})
    conn.execute("UPDATE tickets SET status = 'resolved'")  # tickets live in ServiceNow/SQLite, not this API
    conn.commit()
    assert check_end_state(conn, "s08_airplane_and_data_off").passed


# --- network view and cells -----------------------------------------------------------------

def test_agent_can_read_network_view(client):
    r = client.get("/lines/LIN-2002/network", headers={"X-Actor": "agent"})  # Daniel, airplane mode
    assert r.status_code == 200
    body = r.json()
    assert body["registration_state"] == "not_registered" and body["last_seen"] == "2026-11-25T06:48:00Z"
    assert "airplane_mode" not in body  # symptoms only, no device settings


def test_network_view_unknown_line_404(client):
    assert client.get("/lines/LIN-9999/network").status_code == 404


def test_network_view_follows_device_changes(client):
    assert client.get("/lines/LIN-2001/network").json()["data_session_active"] is True
    client.patch("/devices/DEV-4001", json={"airplane_mode": True}, headers={"X-Actor": "user"})
    n = client.get("/lines/LIN-2001/network", headers={"X-Actor": "agent"}).json()
    assert n["registration_state"] == "not_registered"
    assert n["last_seen"] is not None  # stamped at the moment it dropped off
    client.patch("/devices/DEV-4001", json={"airplane_mode": False})
    assert client.get("/lines/LIN-2001/network").json()["data_session_active"] is True


def test_agent_sees_outages(client):
    r = client.get("/cells", params={"status": "outage"}, headers={"X-Actor": "agent"})
    assert [c["id"] for c in r.json()] == ["CELL-QNS-0107"]
    assert client.get("/cells/CELL-QNS-0107").json()["estimated_restore_at"] == "2026-11-25T14:00:00Z"
    assert client.get("/cells/CELL-NOPE").status_code == 404


def test_agent_cannot_change_cells(client):
    r = client.patch("/cells/CELL-QNS-0107", json={"status": "up", "outage_reason": None}, headers={"X-Actor": "agent"})
    assert r.status_code == 403


def test_ops_can_start_and_end_an_outage(client):
    r = client.patch("/cells/CELL-MAN-0412", json={"status": "outage", "outage_reason": "power"})
    assert r.status_code == 200
    n = client.get("/lines/LIN-2001/network").json()  # Maya is on that cell
    assert n["registration_state"] == "not_registered" and n["last_seen"] is not None
    client.patch("/cells/CELL-MAN-0412", json={"status": "up", "outage_reason": None})
    assert client.get("/lines/LIN-2001/network").json()["registered"] is True


def test_cell_patch_inconsistent_is_409(client):
    assert client.patch("/cells/CELL-MAN-0412", json={"status": "outage"}).status_code == 409


def test_going_abroad_requires_visited_network(client):
    assert client.patch("/devices/DEV-4001", json={"is_abroad": True}).status_code == 409
    r = client.patch("/devices/DEV-4001", json={"is_abroad": True, "visited_network": "Vodafone (UK)"})
    assert r.status_code == 200
