# tests/test_orders.py
# Tests for backend/agent/orders.py (the order state machine) and the database update behind it.

import pytest

from backend.agent import orders
from database import connection, repository

NOW = 1_000_000
STATUSES = ["CREATED", "CAPTURED", "FAILED", "CANCELLED"]
LEGAL = {("CREATED", "CAPTURED"), ("CREATED", "FAILED"), ("CREATED", "CANCELLED")}


@pytest.fixture(autouse=True)
def fresh_database(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "DB_PATH", tmp_path / "test.db")
    connection.init_db()


def add_order(status="CREATED", **fields):
    return repository.insert_order("appr", "PP-123456", status, 3999, NOW, **fields)


def status_of(order_id):
    return repository.get_order(order_id)["status"]


# ---------- the state machine ----------

@pytest.mark.parametrize("target", ["CAPTURED", "FAILED", "CANCELLED"])
def test_a_created_order_can_move_to_each_final_status(target):
    order_id = add_order("CREATED")
    orders.move_order(order_id, target)
    assert status_of(order_id) == target


ILLEGAL = [(a, b) for a in STATUSES for b in STATUSES if (a, b) not in LEGAL]


@pytest.mark.parametrize("start,target", ILLEGAL)
def test_every_other_move_is_refused_and_changes_nothing(start, target):
    order_id = add_order(start)
    with pytest.raises(orders.InvalidMove):
        orders.move_order(order_id, target)
    assert status_of(order_id) == start


@pytest.mark.parametrize("target", ["REFUNDED", "captured", "", None, "CREATED "])
def test_unknown_target_statuses_are_refused(target):
    order_id = add_order("CREATED")
    with pytest.raises(orders.InvalidMove):
        orders.move_order(order_id, target)
    assert status_of(order_id) == "CREATED"


def test_a_missing_order_is_refused():
    with pytest.raises(orders.InvalidMove):
        orders.move_order(9999, "CAPTURED")


def test_an_order_id_given_as_text_works():
    order_id = add_order("CREATED")
    orders.move_order(str(order_id), "CANCELLED")
    assert status_of(order_id) == "CANCELLED"


def test_a_move_can_record_the_capture_details():
    order_id = add_order("CREATED")
    orders.move_order(order_id, "CAPTURED", captured_at=NOW + 50, capture_id="CAP-1")
    order = repository.get_order(order_id)
    assert (order["status"], order["captured_at"], order["capture_id"]) == ("CAPTURED", NOW + 50, "CAP-1")


def test_a_second_capture_is_refused():
    order_id = add_order("CREATED")
    orders.move_order(order_id, "CAPTURED")
    with pytest.raises(orders.InvalidMove):
        orders.move_order(order_id, "CAPTURED")


def test_a_stale_view_cannot_overwrite_a_newer_status(monkeypatch):
    """Two requests race. The second one still sees CREATED, but the order was already captured."""
    order_id = add_order("CAPTURED")
    stale = dict(repository.get_order(order_id), status="CREATED")
    with monkeypatch.context() as patch:                  # the stale view lasts only inside this block
        patch.setattr(repository, "get_order", lambda _id: stale)
        with pytest.raises(orders.InvalidMove):
            orders.move_order(order_id, "FAILED")
    assert status_of(order_id) == "CAPTURED"


# ---------- the database update must not blank other fields ----------

def test_changing_the_status_keeps_the_other_fields():
    order_id = add_order("CREATED", note="waiting", capture_id="CAP-9", captured_at=NOW + 5)
    repository.update_order_status(order_id, "FAILED")
    order = repository.get_order(order_id)
    assert order["status"] == "FAILED"
    assert (order["note"], order["capture_id"], order["captured_at"]) == ("waiting", "CAP-9", NOW + 5)


def test_a_given_field_is_updated():
    order_id = add_order("CREATED")
    repository.update_order_status(order_id, "FAILED", note="PayPal said no")
    assert repository.get_order(order_id)["note"] == "PayPal said no"


def test_update_only_applies_when_the_expected_status_matches():
    order_id = add_order("CREATED")
    assert repository.update_order_status(order_id, "CAPTURED", expected_status="CREATED") == 1
    assert repository.update_order_status(order_id, "FAILED", expected_status="CREATED") == 0
    assert status_of(order_id) == "CAPTURED"