# database/connection.py
import sqlite3
from pathlib import Path

from backend import config

DB_PATH = config.DB_PATH
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_connection():
    db_path = Path(DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()
