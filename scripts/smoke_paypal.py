"""Live check of the PayPal SANDBOX: create a $1.00 order, approve it, capture it.

    python -m scripts.smoke_paypal

You need the sandbox PERSONAL (buyer) account login. No real money is involved.
"""
import sys
import uuid

from backend import config
from backend.services import paypal_service as pp


def main():
    print(config.describe())
    key = f"smoke-{uuid.uuid4().hex[:12]}"
    try:
        order = pp.create_order([pp.LineItem("SafeCart smoke test", 100)], idempotency_key=key,
                                reference_id=key)
    except pp.PayPalError as error:
        print("FAILED to create the order:", error)
        return 1
    print(f"\nCreated sandbox order {order.order_id} ({order.status}) for $1.00")
    print("\n1. Open this link and log in with your sandbox PERSONAL (buyer) account:\n")
    print("   ", order.approve_url)
    print("\n2. Approve the payment. Afterwards the browser may say it cannot reach localhost.")
    print("   That is expected, the approval has already been recorded by PayPal.")
    input("\n3. Press Enter here once you have approved... ")
    try:
        result = pp.capture_order(order.order_id, idempotency_key=key)
    except pp.PayPalError as error:
        print("FAILED to capture:", error)
        return 1
    print(f"\nCapture status: {result.status}  capture id: {result.capture_id}  amount: ${result.amount_cents / 100:.2f}")
    print("SUCCESS: the PayPal sandbox is working." if result.status == "COMPLETED"
          else "Not completed yet. Did you approve the payment in step 2?")
    return 0


if __name__ == "__main__":
    sys.exit(main())
