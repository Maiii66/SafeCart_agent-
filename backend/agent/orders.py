# backend/agent/orders.py
# The order state machine. An order can only move CREATED -> CAPTURED, FAILED or CANCELLED.
# Once it has left CREATED it never moves again. The rule is checked AND applied in one
# database statement, so two requests at the same moment cannot both win.

from database import repository

ALLOWED_MOVES = {
    "CREATED": {"CAPTURED", "FAILED", "CANCELLED"},
}


class InvalidMove(Exception):
    """The order does not exist, or it may not move to that status."""


def move_order(order_id, new_status, captured_at=None, capture_id=None, note=None):
    """Move an order to a new status, or raise InvalidMove. Nothing is ever forced through."""
    order = repository.get_order(order_id)
    if not order:
        raise InvalidMove("No such order.")

    current = order["status"]
    if new_status not in ALLOWED_MOVES.get(current, set()):
        raise InvalidMove(f"An order cannot move from {current} to {new_status}.")

    changed = repository.update_order_status(
        order["id"], new_status,
        captured_at=captured_at, capture_id=capture_id, note=note,
        expected_status=current,
    )
    if changed != 1:
        raise InvalidMove("The order changed while it was being updated.")