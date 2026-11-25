"""Check that your ServiceNow PDI is reachable: one harmless read of the incident table.

    python scripts/check_servicenow.py

Does GET /api/now/table/incident?sysparm_limit=1 with basic auth.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from northwind.integrations import ConfigError, fail, load_env, servicenow_config  # noqa: E402


def main() -> None:
    load_env()
    try:
        base, auth = servicenow_config()
    except ConfigError as e:
        fail(str(e))
    try:
        import requests
    except ImportError:
        fail("requests is not installed. Run: pip install -r requirements-integrations.txt")
    url = f"{base}/api/now/table/incident"
    try:
        r = requests.get(url, auth=auth, headers={"Accept": "application/json"},
                         params={"sysparm_limit": 1, "sysparm_fields": "number,short_description"}, timeout=30)
    except requests.RequestException as e:
        fail(f"Could not reach {base}: {e}. Is the instance awake? (developer.servicenow.com > Manage my Instance)")
    if r.status_code == 401:
        fail("401 Unauthorized: check SERVICENOW_USER / SERVICENOW_PASSWORD (Manage my Instance shows the admin password).")
    if r.status_code != 200 or "application/json" not in r.headers.get("Content-Type", ""):
        fail(f"Unexpected response {r.status_code} from {url}. A hibernating PDI often returns an HTML page; wake it and retry.")
    rows = r.json().get("result", [])
    sample = f" e.g. {rows[0].get('number')}" if rows else ""
    print(f"OK: ServiceNow Table API reachable at {base} ({len(rows)} incident(s) returned{sample}).")


if __name__ == "__main__":
    main()
