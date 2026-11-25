"""Check that STRIPE_SECRET_KEY works: one harmless read (list 1 customer) in test mode.

    python scripts/check_stripe.py

Refuses live keys (sk_live_/rk_live_) before making any network call.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from northwind.integrations import ConfigError, fail, load_env, stripe_test_key  # noqa: E402


def main() -> None:
    load_env()
    try:
        key = stripe_test_key()
    except ConfigError as e:
        fail(str(e))
    try:
        import stripe
    except ImportError:
        fail("stripe is not installed. Run: pip install -r requirements-integrations.txt")
    stripe.api_key = key
    try:
        customers = stripe.Customer.list(limit=1)
    except stripe.AuthenticationError:
        fail("Stripe rejected the key (authentication error). Re-copy the secret key from the sandbox's API keys page.")
    except stripe.StripeError as e:
        fail(f"Stripe call failed: {e.user_message or e}")
    if customers.data and customers.data[0].livemode:
        fail("Stripe returned live-mode data. Stopping.")
    print(f"OK: Stripe test mode reachable ({len(customers.data)} customer(s) returned by list(limit=1)).")


if __name__ == "__main__":
    main()
