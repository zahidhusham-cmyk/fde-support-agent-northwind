import os

import requests

from northwind.integrations import load_env

# Exact whole-line replies. Anything else is a message to the agent, not a phone change.
_ACTIONS = {
    "airplane on",
    "airplane off",
    "mobile data on",
    "mobile data off",
    "data roaming on",
    "data roaming off",
    "restart",
    "reseat sim",
}


def process_customer_action(action: str, line_id: str) -> str | None:
    """Apply one exact customer phrase to the phone on this line.

    Matching ignores case. A reply that is not one of the action lines returns
    None and does not call the network API.
    """
    phrase = (action or "").strip().lower()
    if phrase not in _ACTIONS:
        return None
    device_id, error = get_device_id(line_id)
    if error:
        return error
    if phrase == "airplane on":
        return modify_device_airplane_mode(device_id, True)
    if phrase == "airplane off":
        return modify_device_airplane_mode(device_id, False)
    if phrase == "mobile data on":
        return modify_device_data(device_id, True)
    if phrase == "mobile data off":
        return modify_device_data(device_id, False)
    if phrase == "data roaming on":
        return modify_device_data_roaming(device_id, True)
    if phrase == "data roaming off":
        return modify_device_data_roaming(device_id, False)
    if phrase == "restart":
        return restart_device(device_id)
    return reseat_sim(device_id)


def get_device_id(line_id: str) -> tuple[str | None, str | None]:
    """Look up the handset on a line.

    GET /lines/{line_id}/device as the customer. Returns (device id, None) or
    (None, error message). The device id is the "id" field, not "device_id".
    """
    if not line_id:
        return None, "pass line_id"
    base = _base()
    try:
        response = requests.get(
            f"{base}/lines/{line_id}/device", headers=_user_headers(), timeout=30
        )
    except requests.RequestException as exc:
        return None, f"network API unreachable at {base}: {exc}"
    if response.status_code == 404:
        return None, f"no device on line {line_id}"
    if response.status_code != 200:
        return None, f"network API returned {response.status_code}: {response.text[:200]}"
    return response.json()["id"], None


def modify_device_airplane_mode(device_id: str, airplane_mode: bool) -> str:
    """Set airplane mode on the handset. False is a real value, not a missing one."""
    return _patch_device(device_id, {"airplane_mode": airplane_mode}, "Airplane mode modified")


def modify_device_data(device_id: str, data_enabled: bool) -> str:
    """Set the phone's mobile-data switch. The API field is mobile_data_enabled."""
    return _patch_device(device_id, {"mobile_data_enabled": data_enabled}, "Data modified")


def modify_device_data_roaming(device_id: str, data_roaming_enabled: bool) -> str:
    """Set the phone's data-roaming switch. The API field is data_roaming_on."""
    return _patch_device(
        device_id, {"data_roaming_on": data_roaming_enabled}, "Data roaming modified"
    )


def restart_device(device_id: str) -> str:
    """Clear a stale radio by marking the phone as restarted."""
    return _patch_device(device_id, {"needs_restart": False}, "Device restarted")


def reseat_sim(device_id: str) -> str:
    """Mark the SIM as seated again."""
    return _patch_device(device_id, {"sim_ok": True}, "SIM reseated")


def _base() -> str:
    load_env()
    return os.getenv("NETWORK_API_URL", "http://localhost:8300").rstrip("/")


def _user_headers() -> dict:
    return {"X-Actor": "user", "Accept": "application/json"}


def _patch_device(device_id: str, fields: dict, success: str) -> str:
    """PATCH /devices/{id} as the customer and report the HTTP result."""
    if not device_id:
        return "pass device_id"
    base = _base()
    try:
        response = requests.patch(
            f"{base}/devices/{device_id}",
            headers=_user_headers(),
            json=fields,
            timeout=30,
        )
    except requests.RequestException as exc:
        return f"network API unreachable at {base}: {exc}"
    if response.status_code != 200:
        return f"network API returned {response.status_code}: {response.text[:200]}"
    return success
