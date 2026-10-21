"""SQLite persistence layer: orders, tasks, audit log, config, uploads, data issues.

Everything lives in d2c.db next to this file, so tasks and audit records survive
a browser refresh or an app restart.
"""
import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "d2c.db")

TASK_STATUSES = ["Open", "In Progress", "Resolved"]

DEFAULT_CONFIG = {
    "unconfirmed_hours": "2",        # Rule 1
    "dispatch_hours": "24",          # Rule 2
    "high_value_threshold": "5000",  # Rule 5
    "deadline_hours_high": "4",      # task deadline = as-of time + N hours
    "deadline_hours_medium": "24",
    "deadline_hours_low": "48",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    order_date TEXT, customer_name TEXT, phone TEXT, order_value REAL,
    payment_type TEXT, status TEXT, dispatch_date TEXT,
    expected_delivery_date TEXT, assigned_employee TEXT, upload_id INTEGER
);
CREATE TABLE IF NOT EXISTS uploads (
    upload_id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT, uploaded_at TEXT, uploaded_by TEXT,
    total_rows INTEGER, valid_rows INTEGER, issue_rows INTEGER
);
CREATE TABLE IF NOT EXISTS data_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id INTEGER, row_number INTEGER, order_id TEXT,
    issues TEXT, raw_row TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
    task_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    rule_type TEXT NOT NULL,
    customer_name TEXT, phone TEXT, order_value REAL, order_date TEXT,
    reason TEXT, priority TEXT, assigned_employee TEXT,
    action_required TEXT, deadline TEXT,
    status TEXT DEFAULT 'Open',
    created_at TEXT, updated_at TEXT,
    UNIQUE (order_id, rule_type)          -- prevents duplicate tasks on re-upload
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT, action TEXT, timestamp TEXT, user TEXT
);
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY, value TEXT
);
"""


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    for k, v in DEFAULT_CONFIG.items():
        conn.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", (k, v))
    conn.commit()
    conn.close()


def get_config(conn):
    cfg = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM config")}
    return {
        "unconfirmed_hours": float(cfg["unconfirmed_hours"]),
        "dispatch_hours": float(cfg["dispatch_hours"]),
        "high_value_threshold": float(cfg["high_value_threshold"]),
        "deadline_hours_high": float(cfg["deadline_hours_high"]),
        "deadline_hours_medium": float(cfg["deadline_hours_medium"]),
        "deadline_hours_low": float(cfg["deadline_hours_low"]),
    }


def set_config(conn, key, value, user):
    old = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, str(value)))
    log_audit(conn, "CONFIG", f"Rule setting '{key}' changed from {old['value'] if old else '-'} to {value}", user)


def log_audit(conn, order_id, action, user):
    conn.execute(
        "INSERT INTO audit_log (order_id, action, timestamp, user) VALUES (?, ?, ?, ?)",
        (order_id, action, now_str(), user),
    )


def reset_all(conn):
    for t in ("orders", "uploads", "data_issues", "tasks", "audit_log"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM sqlite_sequence")
    conn.commit()
