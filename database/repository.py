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
