# database/repository.py
# The only file that writes SQL. Always uses ? placeholders, never string-building.

from database import connection


def add_log(actor, action, detail, ref=None):
    connection.init_db()
    conn = connection.get_connection()
    try:
        conn.execute("INSERT INTO action_log (actor, action, detail, ref) VALUES (?, ?, ?, ?)",
                     (actor, action, str(detail)[:1000], ref))
        conn.commit()
    finally:
        conn.close()


def list_logs(limit=200):
    connection.init_db()
    conn = connection.get_connection()
    try:
        rows = conn.execute("SELECT * FROM action_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

# Phase 2
def insert_approval(approval_id, payload, signature, created_at, expires_at):
    connection.init_db()
    conn = connection.get_connection()
    try:
        conn.execute(
            "INSERT INTO approvals (id, payload, signature, created_at, expires_at, used_at, step_up_confirmed) VALUES (?, ?, ?, ?, ?, NULL, 0)",
            (approval_id, payload, signature, created_at, expires_at),
        )
        conn.commit()
    finally:
        conn.close()


def get_approval(approval_id):
    connection.init_db()
    conn = connection.get_connection()
    try:
        row = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def mark_approval_used(approval_id, used_at):
    connection.init_db()
    conn = connection.get_connection()
    try:
        cur = conn.execute("UPDATE approvals SET used_at=? WHERE id=? AND used_at IS NULL", (used_at, approval_id))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def set_step_up_confirmed(approval_id, confirmed=1):
    connection.init_db()
    conn = connection.get_connection()
    try:
        conn.execute("UPDATE approvals SET step_up_confirmed=? WHERE id=?", (confirmed, approval_id))
        conn.commit()
    finally:
        conn.close()


def insert_order(approval_id, paypal_order_id, status, total_cents, created_at, captured_at=None, capture_id=None, note=None):
    connection.init_db()
    conn = connection.get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO orders (approval_id, paypal_order_id, status, total_cents, created_at, captured_at, capture_id, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (approval_id, paypal_order_id, status, total_cents, created_at, captured_at, capture_id, note),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_order(order_id):
    connection.init_db()
    conn = connection.get_connection()
    try:
        if isinstance(order_id, int) or (isinstance(order_id, str) and order_id.isdigit()):
            row = conn.execute("SELECT * FROM orders WHERE id = ?", (int(order_id),)).fetchone()
        else:
            row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_orders(limit=200):
    connection.init_db()
    conn = connection.get_connection()
    try:
        rows = conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def update_order_status(order_id, new_status, captured_at=None, capture_id=None, note=None,
                        expected_status=None):
    """Change an order's status. Returns how many rows changed (0 or 1).

    Only the fields you pass are changed: leaving captured_at, capture_id or note as None
    keeps whatever is stored. If expected_status is given, the change is made ONLY when the
    order is still in that status, and the check and the change happen in one statement, so
    two requests at the same moment cannot both succeed.
    """
    sql = ("UPDATE orders SET status = ?, captured_at = COALESCE(?, captured_at), "
           "capture_id = COALESCE(?, capture_id), note = COALESCE(?, note) WHERE id = ?")
    params = [new_status, captured_at, capture_id, note, order_id]
    if expected_status is not None:
        sql += " AND status = ?"
        params.append(expected_status)
    connection.init_db()
    conn = connection.get_connection()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def spent_since(now_minus_seconds, statuses):
    connection.init_db()
    conn = connection.get_connection()
    try:
        if not statuses:
            return 0
        placeholders = ",".join("?" for _ in statuses)
        sql = f"SELECT COALESCE(SUM(total_cents), 0) as s FROM orders WHERE created_at >= ? AND status IN ({placeholders})"
        row = conn.execute(sql, (now_minus_seconds, *statuses)).fetchone()
        return int(row["s"] if row else 0)
    finally:
        conn.close()


def count_orders_since(now_minus_seconds, statuses=None):
    connection.init_db()
    conn = connection.get_connection()
    try:
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql = f"SELECT COUNT(*) as c FROM orders WHERE created_at >= ? AND status IN ({placeholders})"
            row = conn.execute(sql, (now_minus_seconds, *statuses)).fetchone()
        else:
            sql = "SELECT COUNT(*) as c FROM orders WHERE created_at >= ?"
            row = conn.execute(sql, (now_minus_seconds,)).fetchone()
        return int(row["c"] if row else 0)
    finally:
        conn.close()


def get_setting(key):
    connection.init_db()
    conn = connection.get_connection()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def set_setting(key, value):
    connection.init_db()
    conn = connection.get_connection()
    try:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()
    finally:
        conn.close()
