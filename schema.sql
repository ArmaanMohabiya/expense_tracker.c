-- Database schema (SQLite). Created automatically the first time the app starts.

CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    username       TEXT NOT NULL UNIQUE COLLATE NOCASE,   -- "Alice" and "alice" are the same user
    password_hash  TEXT NOT NULL,                         -- never the password itself
    token_version  INTEGER NOT NULL DEFAULT 0,            -- raised on password change to sign out other devices
    created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS expenses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date          TEXT NOT NULL,                         -- YYYY-MM-DD
    category      TEXT NOT NULL,
    amount_paise  INTEGER NOT NULL CHECK (amount_paise > 0),  -- Rs 120.50 is stored as 12050
    note          TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_expenses_user_date ON expenses (user_id, date);

-- One optional spending limit per user per month.
CREATE TABLE IF NOT EXISTS budgets (
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    month        TEXT NOT NULL,                          -- YYYY-MM
    limit_paise  INTEGER NOT NULL CHECK (limit_paise > 0),
    PRIMARY KEY (user_id, month)
);
