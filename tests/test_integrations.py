import subprocess
import sys
from pathlib import Path

import pytest

from northwind.integrations import ConfigError, servicenow_config, stripe_test_key
from northwind.seed import seed

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import sync_servicenow  # noqa: E402
import sync_stripe  # noqa: E402


@pytest.mark.parametrize("key", ["sk_live_abc123", "rk_live_abc123"])
def test_live_stripe_key_is_refused(monkeypatch, key):
    monkeypatch.setenv("STRIPE_SECRET_KEY", key)
    with pytest.raises(ConfigError, match="LIVE"):
        stripe_test_key()


@pytest.mark.parametrize("key", ["sk_test_abc123", "rk_test_abc123"])
def test_test_stripe_key_is_accepted(monkeypatch, key):
    monkeypatch.setenv("STRIPE_SECRET_KEY", key)
    assert stripe_test_key() == key


@pytest.mark.parametrize("key", ["", "pk_test_abc", "garbage"])
def test_missing_or_wrong_stripe_key_is_refused(monkeypatch, key):
    monkeypatch.setenv("STRIPE_SECRET_KEY", key)
    with pytest.raises(ConfigError):
        stripe_test_key()


@pytest.mark.parametrize("script", ["check_stripe.py", "sync_stripe.py"])
def test_scripts_refuse_live_key_before_any_network_call(script, tmp_path):
    db = tmp_path / "x.db"
    seed(db).close()
    env = {"STRIPE_SECRET_KEY": "sk_live_should_never_be_used", "PATH": "/usr/bin:/bin"}
    args = [sys.executable, str(ROOT / "scripts" / script)] + (["--db", str(db)] if script == "sync_stripe.py" else [])
    r = subprocess.run(args, env=env, capture_output=True, text=True, cwd=tmp_path, timeout=60)
    assert r.returncode == 1
    assert "LIVE key" in r.stderr
    assert "sk_live_should_never_be_used" not in r.stdout + r.stderr  # never echo the key


def test_servicenow_instance_name_expands_to_url(monkeypatch):
    monkeypatch.setenv("SERVICENOW_INSTANCE", "dev12345")
    monkeypatch.setenv("SERVICENOW_USER", "admin")
    monkeypatch.setenv("SERVICENOW_PASSWORD", "x")
    assert servicenow_config() == ("https://dev12345.service-now.com", ("admin", "x"))


def test_servicenow_rejects_plain_http(monkeypatch):
    monkeypatch.setenv("SERVICENOW_INSTANCE", "http://dev12345.service-now.com")
    monkeypatch.setenv("SERVICENOW_USER", "admin")
    monkeypatch.setenv("SERVICENOW_PASSWORD", "x")
    with pytest.raises(ConfigError):
        servicenow_config()


def test_stripe_plan_from_seed(tmp_path):
    plan = sync_stripe.build_plan(seed(tmp_path / "s.db"), "seed")
    assert len(plan) == 12 and sum(len(e["invoices"]) for e in plan) == 36
    liam = next(e for e in plan if e["customer"]["name"] == "Liam O'Connor")
    overdue = [i for i in liam["invoices"] if i["metadata"]["northwind_overdue"] == "1"]
    assert len(overdue) == 1 and overdue[0]["paid"] is False
    assert all(v is None or isinstance(v, str) for i in liam["invoices"] for v in i["metadata"].values())


def test_servicenow_payloads_from_seed(tmp_path):
    payloads = sync_servicenow.build_payloads(seed(tmp_path / "s.db"), "seed")
    assert len(payloads) == 9
    assert payloads[0]["correlation_id"] == "northwind-seed-TKT-5001"
    assert len({p["correlation_id"] for p in payloads}) == 9
    assert all(p["state"] == "1" for p in payloads)
