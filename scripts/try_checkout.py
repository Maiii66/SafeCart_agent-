"""Run one real PayPal sandbox checkout through every SafeCart safety check.

Usage: python -m scripts.try_checkout [product_id]
Uses a temporary database, so earlier runs cannot affect the daily cap.
"""

import os
import shutil
import sys
import tempfile
import time

from backend import config
from backend.agent import approvals, checkout, step_up
from backend.models import Candidate
from backend.money import format_usd
from backend.services import catalog_demo
from database import connection, repository

DEFAULT_PRODUCT = "demo-bud-04"
BUDGET_CENTS = 5000
MAX_CAPTURE_ATTEMPTS = 3


def main(argv=None, ask=input, say=print):
    if argv is None:
        argv = sys.argv[1:]
    product_id = argv[0] if argv else DEFAULT_PRODUCT

    if not config.paypal_enabled():
        say("PayPal keys are missing. Add PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET to .env first.")
        return 1
    if not config.is_sandbox():
        say("SafeCart only makes payments in the PayPal sandbox. Set PAYPAL_ENV=sandbox.")
        return 1
    if not config.signing_secret_ok():
        say("SIGNING_SECRET is missing or shorter than 32 characters.")
        return 1

    product = catalog_demo.get_product(product_id)
    if product is None:
        say(f"{product_id} is not in the demo catalog.")
        return 1
    if not product.offers:
        say(f"{product_id} has no offer to buy.")
        return 1

    original_db_path = connection.DB_PATH
    temp_dir = tempfile.mkdtemp(prefix="safecart_checkout_")
    try:
        connection.DB_PATH = os.path.join(temp_dir, "try_checkout.db")
        connection.init_db()

        candidate = Candidate(product, product.offers[0])
        approval = approvals.create_approval([candidate], BUDGET_CENTS, "demo", now=time.time())
        if not approval.ok:
            say(f"Could not create an approval ({approval.code}): {approval.reason}")
            return 1

        if approval.payload.get("step_up_required"):
            say("This purchase needs a typed confirmation:")
            for reason in approval.payload.get("step_up_reasons", ()):
                say(f"- {reason}")
            total_text = format_usd(approval.payload["total_cents"])
            typed = ask(f"Type the exact total ({total_text}) or CONFIRM: ")
            verdict = step_up.confirm_step_up(approval.approval_id, typed, now=time.time())
            if not verdict.ok:
                say(f"Not confirmed ({verdict.code}): {verdict.reason}")
                return 1

        created = checkout.execute_checkout(approval.approval_id, now=time.time())
        if not created.ok:
            say(f"Checkout refused ({created.code}): {created.reason}")
            return 1

        say(f"Approve the payment here: {created.approve_url}")
        say("Log in with the PayPal sandbox PERSONAL (buyer) account and approve the payment.")
        ask("Press Enter here once you have approved... ")

        capture = None
        for attempt in range(MAX_CAPTURE_ATTEMPTS):
            capture = checkout.complete_order(created.order_id, now=time.time())
            if capture.ok or capture.code != "WAITING_APPROVAL":
                break
            say("PayPal says the payment has not been approved yet.")
            if attempt < MAX_CAPTURE_ATTEMPTS - 1:
                ask("Press Enter to try again... ")

        order = repository.get_order(created.order_id)
        say(f"Result: {capture.code}: {capture.reason}")
        if order:
            say(f"Order row: status={order['status']}, total={format_usd(order['total_cents'])}, "
                f"capture id={order['capture_id'] or 'none'}")
        else:
            say("Order row: none")
        say("Last 8 audit log lines:")
        for row in repository.list_logs(8):
            say(f"  [{row['actor']}] {row['action']}: {row['detail']}")

        captured = bool(order and order["status"] == "CAPTURED" and capture.ok)
        if captured:
            say("SUCCESS: a full PayPal sandbox payment went through every safety check.")
            return 0
        say("The payment did not complete.")
        return 1
    finally:
        connection.DB_PATH = original_db_path
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
