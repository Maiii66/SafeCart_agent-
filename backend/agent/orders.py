# backend/agent/orders.py
# Order state machine.

ALLOWED_MOVES = {
    "CREATED": {"CAPTURED", "FAILED", "CANCELLED"},
}


class InvalidMove(Exception):
    pass


def move_order(order_id, new_status, repo=None):
    from database import repository as repo_mod
    repo = repo_mod if repo is None else repo
    order = repo.get_order(order_id)
    if not order:
        raise InvalidMove("No such order")
    cur = order["status"]
    allowed = ALLOWED_MOVES.get(cur, set())
    if new_status not in allowed:
        raise InvalidMove(f"Invalid move {cur} -> {new_status}")
    return repo.update_order_status(order_id, new_status)