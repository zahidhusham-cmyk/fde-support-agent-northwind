"""Small helpers shared by the scripts that talk to external systems.

Only config and safety checks live here. Nothing in this file is an agent tool.
"""
from __future__ import annotations

import os
import sys

from northwind.db import ROOT


class ConfigError(RuntimeError):
    """A required setting is missing or unsafe."""


def load_env() -> None:
    """Load ./.env if python-dotenv is installed. Real env vars win over the file."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env", override=False)


def require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is not set. Copy .env.example to .env and fill it in.")
    return value


def stripe_test_key() -> str:
    """Return STRIPE_SECRET_KEY, refusing anything that isn't a test-mode (sandbox) key."""
    key = require("STRIPE_SECRET_KEY")
    if key.startswith(("sk_live_", "rk_live_")):
        raise ConfigError("STRIPE_SECRET_KEY is a LIVE key. This project only runs against Stripe test mode. "
                          "Use the sk_test_... key from your sandbox instead.")
    if not key.startswith(("sk_test_", "rk_test_")):
        raise ConfigError("STRIPE_SECRET_KEY must start with sk_test_ (or rk_test_ for a restricted test key).")
    return key


def servicenow_config() -> tuple[str, tuple[str, str]]:
    """Return (base_url, (user, password)). SERVICENOW_INSTANCE may be 'dev12345' or a full URL."""
    instance = require("SERVICENOW_INSTANCE").rstrip("/")
    if not instance.startswith("http"):
        instance = f"https://{instance}.service-now.com"
    if not instance.startswith("https://"):
        raise ConfigError("SERVICENOW_INSTANCE must use https.")
    return instance, (require("SERVICENOW_USER"), require("SERVICENOW_PASSWORD"))


def fail(message: str, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(code)
