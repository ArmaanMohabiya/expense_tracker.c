"""Database layer: the only module that opens the SQLite file.
(Same role as storage.c in the C version.)"""
import sqlite3

from flask import current_app, g


def get_db():
    """One connection per request, created on first use."""
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row            # lets us write row["amount_paise"]
        g.db.execute("PRAGMA foreign_keys = ON")  # SQLite needs this switched on per connection
    return g.db


def close_db(error=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = get_db()
    with current_app.open_resource("schema.sql") as f:
        conn.executescript(f.read().decode("utf-8"))
    # Databases made by an earlier version of the app lack newer columns: add them.
    user_columns = [row["name"] for row in conn.execute("PRAGMA table_info(users)")]
    if "token_version" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0")
    conn.commit()


def init_app(app):
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()
