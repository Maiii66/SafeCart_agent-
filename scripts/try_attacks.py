# scripts/try_attacks.py
# Run as python -m scripts.try_attacks. No keys, no network.

import json
import os
import tempfile
import time

from backend import config
from backend.services import paypal_service
from database import connection, repository
from backend.agent import approvals, checkout, limits
from backend.models import Candidate, Offer
from backend.services import catalog_demo
from backend.services import catalog


def fake_create_order(items, idempotency_key, reference_id="safecart-order"):
    total = sum(getattr(i, "unit_cents", 0) for i in items)
    print(f"[fake PayPal] create_order total={total} cents key={idempotency_key}")
    class R:
        order_id = "FAKE-ORDER-123"
        approve_url = "https://www.sandbox.paypal.com/checkoutnow?token=FAKE-ORDER-123"
    return R()


def fake_capture_order(order_id, idempotency_key):
    print(f"[fake PayPal] capture_order {order_id} key={idempotency_key}")
    class R:
        status = "COMPLETED"
        capture_id = "FAKE-CAP"
        amount_cents = 3999
    return R()


def make_candidate(pid="demo-earbuds", domain=None):
    p = catalog.get_live_product(pid, "demo")
    dom = domain if domain is not None else getattr(p, "domain", "demo-shop.example")
    offer = Offer(offer_id=p.offer_id, product_id=p.product_id, title=p.title, price_cents=p.price_cents, currency=p.currency, availability=p.availability, condition=p.condition, domain=dom, shipping_cents=0)
    return Candidate(product=p, offer=offer, score=1.0, reasons=[])


def main():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        os.unlink(path)
    except Exception:
        pass
    connection.DB_PATH = path
    connection.init_db()
    config.SIGNING_SECRET = "a" * 64
    config.USE_DEMO_CATALOG = True
    paypal_service.create_order = fake_create_order
    paypal_service.capture_order = fake_capture_order
    limits.set_paused(False)

    fixed = time.time()

    # 1
    try:
        catalog_demo.set_price_drift("demo-earbuds", None)
        c = make_candidate("demo-earbuds", "demo-shop.example")
        ar = approvals.create_approval([c], 5000, "demo", flags={}, now=fixed)
        if ar.ok:
            resv = approvals.verify_approval(ar.approval_id, now=fixed)
            if isinstance(resv, tuple) and resv[0].ok and resv[1].get("step_up_required"):
                from backend.agent import step_up
                step_up.confirm_step_up(ar.approval_id, "CONFIRM", now=fixed)
            co = checkout.execute_checkout(ar.approval_id, now=fixed)
            if co.ok:
                print("ACCEPTED")
            else:
                print(f"REFUSED ({co.code}): {co.reason}")
        else:
            print(f"REFUSED ({ar.code}): {ar.reason}")
    except Exception as e:
        print(f"REFUSED (ERR): {e}")

    # 2 replay
    try:
        limits.set_paused(False)
        catalog_demo.set_price_drift("demo-earbuds", None)
        c = make_candidate("demo-earbuds", "demo-shop.example")
        ar = approvals.create_approval([c], 5000, "demo", flags={}, now=fixed)
        if ar.ok:
            resv = approvals.verify_approval(ar.approval_id, now=fixed)
            if isinstance(resv, tuple) and resv[0].ok and resv[1].get("step_up_required"):
                from backend.agent import step_up
                step_up.confirm_step_up(ar.approval_id, "CONFIRM", now=fixed)
            co1 = checkout.execute_checkout(ar.approval_id, now=fixed)
            co2 = checkout.execute_checkout(ar.approval_id, now=fixed)
            if co2.ok:
                print("ACCEPTED")
            else:
                print(f"REFUSED ({co2.code}): {co2.reason}")
        else:
            print(f"REFUSED ({ar.code}): {ar.reason}")
    except Exception as e:
        print(f"REFUSED (ERR): {e}")

    # 3 tampered
    try:
        limits.set_paused(False)
        catalog_demo.set_price_drift("demo-earbuds", None)
        c = make_candidate("demo-earbuds", "demo-shop.example")
        ar = approvals.create_approval([c], 5000, "demo", flags={}, now=fixed)
        row = repository.get_approval(ar.approval_id)
        payload = json.loads(row["payload"])
        payload["total_cents"] = int(payload["total_cents"]) + 1
        conn = connection.get_connection()
        try:
            conn.execute("UPDATE approvals SET payload=? WHERE id=?", (json.dumps(payload, sort_keys=True, separators=(",", ":")), ar.approval_id))
            conn.commit()
        finally:
            conn.close()
        co = checkout.execute_checkout(ar.approval_id, now=fixed)
        if co.ok:
            print("ACCEPTED")
        else:
            print(f"REFUSED ({co.code}): {co.reason}")
    except Exception as e:
        print(f"REFUSED (ERR): {e}")

    # 4 expired
    try:
        limits.set_paused(False)
        catalog_demo.set_price_drift("demo-earbuds", None)
        c = make_candidate("demo-earbuds", "demo-shop.example")
        ar = approvals.create_approval([c], 5000, "demo", flags={}, now=fixed)
        resv = approvals.verify_approval(ar.approval_id, now=fixed + 601)
        if isinstance(resv, tuple):
            v, p = resv[0], resv[1]
            if v.ok:
                print("ACCEPTED")
            else:
                print(f"REFUSED ({v.code}): {v.reason}")
        else:
            if resv.ok:
                print("ACCEPTED")
            else:
                print(f"REFUSED ({resv.code}): {resv.reason}")
    except Exception as e:
        print(f"REFUSED (ERR): {e}")

    # 5 price drift
    try:
        limits.set_paused(False)
        catalog_demo.set_price_drift("demo-earbuds", None)
        c = make_candidate("demo-earbuds", "demo-shop.example")
        ar = approvals.create_approval([c], 5000, "demo", flags={}, now=time.time())
        if not ar.ok:
            print(f"REFUSED ({ar.code}): {ar.reason}")
        else:
            catalog_demo.set_price_drift("demo-earbuds", 5999)
            resv = approvals.verify_approval(ar.approval_id, now=time.time())
            if isinstance(resv, tuple) and resv[0].ok and resv[1].get("step_up_required"):
                from backend.agent import step_up
                step_up.confirm_step_up(ar.approval_id, "CONFIRM", now=time.time())
            co = checkout.execute_checkout(ar.approval_id, now=time.time())
            if co.ok:
                print("ACCEPTED")
            else:
                print(f"REFUSED ({co.code}): {co.reason}")
    except Exception as e:
        print(f"REFUSED (ERR): {e}")

    # 6 over daily cap
    try:
        limits.set_paused(False)
        catalog_demo.set_price_drift("demo-earbuds", None)
        config.DAILY_CAP_CENTS = 50  # 50 cents cap
        c = make_candidate("demo-earbuds", "demo-shop.example")
        ar = approvals.create_approval([c], 5000, "demo", flags={}, now=time.time())
        if ar.ok:
            resv = approvals.verify_approval(ar.approval_id, now=time.time())
            if isinstance(resv, tuple) and resv[0].ok and resv[1].get("step_up_required"):
                from backend.agent import step_up
                step_up.confirm_step_up(ar.approval_id, "CONFIRM", now=time.time())
            co = checkout.execute_checkout(ar.approval_id, now=time.time())
            if co.ok:
                print("ACCEPTED")
            else:
                print(f"REFUSED ({co.code}): {co.reason}")
        else:
            print(f"REFUSED ({ar.code}): {ar.reason}")
    except Exception as e:
        print(f"REFUSED (ERR): {e}")

    # reset cap for rest
    config.DAILY_CAP_CENTS = 10000

    # 7 paused
    try:
        limits.set_paused(False)
        catalog_demo.set_price_drift("demo-earbuds", None)
        limits.set_paused(True)
        c = make_candidate("demo-earbuds", "demo-shop.example")
        ar = approvals.create_approval([c], 5000, "demo", flags={}, now=time.time())
        if ar.ok:
            resv = approvals.verify_approval(ar.approval_id, now=time.time())
            if isinstance(resv, tuple) and resv[0].ok and resv[1].get("step_up_required"):
                from backend.agent import step_up
                step_up.confirm_step_up(ar.approval_id, "CONFIRM", now=time.time())
            co = checkout.execute_checkout(ar.approval_id, now=time.time())
            if co.ok:
                print("ACCEPTED")
            else:
                print(f"REFUSED ({co.code}): {co.reason}")
        else:
            print(f"REFUSED ({ar.code}): {ar.reason}")
    except Exception as e:
        print(f"REFUSED (ERR): {e}")
    finally:
        limits.set_paused(False)

    # 8 step-up without confirmation
    try:
        limits.set_paused(False)
        catalog_demo.set_price_drift("demo-earbuds", None)
        # make it need step up - over threshold
        config.STEP_UP_THRESHOLD_CENTS = 10
        c = make_candidate("demo-earbuds", "demo-shop.example")
        ar = approvals.create_approval([c], 5000, "demo", flags={}, now=time.time())
        if ar.ok:
            co = checkout.execute_checkout(ar.approval_id, now=time.time())
            if co.ok:
                print("ACCEPTED")
            else:
                print(f"REFUSED ({co.code}): {co.reason}")
        else:
            print(f"REFUSED ({ar.code}): {ar.reason}")
        config.STEP_UP_THRESHOLD_CENTS = 2500
    except Exception as e:
        print(f"REFUSED (ERR): {e}")

    # 9 wrong then right
    try:
        limits.set_paused(False)
        catalog_demo.set_price_drift("demo-earbuds", None)
        config.STEP_UP_THRESHOLD_CENTS = 10
        c = make_candidate("demo-earbuds", "demo-shop.example")
        ar = approvals.create_approval([c], 5000, "demo", flags={}, now=time.time())
        if ar.ok:
            from backend.agent import step_up
            sr = step_up.confirm_step_up(ar.approval_id, "WRONG", now=time.time())
            if sr.ok:
                print("ACCEPTED")
            else:
                print(f"REFUSED ({sr.code}): {sr.reason}")
            sr2 = step_up.confirm_step_up(ar.approval_id, "39.99", now=time.time())
            if sr2.ok:
                co = checkout.execute_checkout(ar.approval_id, now=time.time())
                if co.ok:
                    print("ACCEPTED")
                else:
                    print(f"REFUSED ({co.code}): {co.reason}")
            else:
                print(f"REFUSED ({sr2.code}): {sr2.reason}")
        else:
            print(f"REFUSED ({ar.code}): {ar.reason}")
        config.STEP_UP_THRESHOLD_CENTS = 2500
    except Exception as e:
        print(f"REFUSED (ERR): {e}")

    # 10 unknown merchant needs step-up
    try:
        limits.set_paused(False)
        catalog_demo.set_price_drift("demo-earbuds", None)
        config.STEP_UP_THRESHOLD_CENTS = 999999
        c = make_candidate("demo-earbuds", "evilbestbuy.com")
        ar = approvals.create_approval([c], 5000, "demo", flags={}, now=time.time())
        if ar.ok and ar.payload and ar.payload.get("step_up_required"):
            print("REFUSED (STEP_UP_REQUIRED): step up required")
        elif ar.ok:
            print("ACCEPTED")
        else:
            print(f"REFUSED ({ar.code}): {ar.reason}")
        config.STEP_UP_THRESHOLD_CENTS = 2500
    except Exception as e:
        print(f"REFUSED (ERR): {e}")


if __name__ == "__main__":
    main()