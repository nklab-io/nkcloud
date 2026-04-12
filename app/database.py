import json
import os
import sqlite3
import time
from contextlib import contextmanager

from . import config


def init_db():
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    with sqlite3.connect(config.DB_PATH) as conn:
        # --- Original table ---
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shares (
                id TEXT PRIMARY KEY,
                token TEXT UNIQUE NOT NULL,
                path TEXT NOT NULL,
                is_directory INTEGER NOT NULL DEFAULT 0,
                password_hash TEXT,
                expires_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                download_count INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_shares_token ON shares(token)")

        # --- Shares migration: add new columns ---
        _add_column(conn, "shares", "type", "TEXT NOT NULL DEFAULT 'file_download'")
        _add_column(conn, "shares", "created_by", "TEXT")
        _add_column(conn, "shares", "pw_version", "INTEGER NOT NULL DEFAULT 1")

        # --- Users table ---
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                quota_bytes INTEGER NOT NULL DEFAULT 0,
                used_bytes INTEGER NOT NULL DEFAULT 0,
                is_disabled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username)")

        # --- Invites table ---
        conn.execute("""
            CREATE TABLE IF NOT EXISTS invites (
                id TEXT PRIMARY KEY,
                token TEXT UNIQUE NOT NULL,
                created_by TEXT NOT NULL,
                expires_at TEXT,
                used_at TEXT,
                used_by TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_invites_token ON invites(token)")

        # --- Login attempts (persistent rate limiting) ---
        conn.execute("""
            CREATE TABLE IF NOT EXISTS login_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip TEXT NOT NULL,
                attempted_at REAL NOT NULL,
                success INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_login_attempts_ip ON login_attempts(ip)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_login_attempts_time ON login_attempts(attempted_at)")
        # Migration: add username + user_agent columns if missing
        cols = {r[1] for r in conn.execute("PRAGMA table_info(login_attempts)").fetchall()}
        if "username" not in cols:
            conn.execute("ALTER TABLE login_attempts ADD COLUMN username TEXT")
        if "user_agent" not in cols:
            conn.execute("ALTER TABLE login_attempts ADD COLUMN user_agent TEXT")

        # --- Audit log ---
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                user_id TEXT,
                username TEXT,
                action TEXT NOT NULL,
                target_path TEXT,
                detail TEXT,
                ip TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_user ON audit_log(user_id)")

        conn.commit()


def _add_column(conn, table: str, column: str, definition: str):
    """Add a column to a table if it doesn't exist (idempotent)."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except sqlite3.OperationalError:
        pass  # column already exists


@contextmanager
def get_db():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def has_any_users() -> bool:
    with get_db() as conn:
        row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
        return row[0] > 0


def get_user_by_id(user_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_username(username: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None


def get_owner_user() -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE role = 'owner'").fetchone()
        return dict(row) if row else None


def record_audit(user_id: str | None, username: str | None, action: str,
                 target_path: str | None = None, detail: str | None = None,
                 ip: str | None = None):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO audit_log (user_id, username, action, target_path, detail, ip) VALUES (?,?,?,?,?,?)",
            (user_id, username, action, target_path, detail, ip),
        )
        conn.commit()


def check_rate_limit(ip: str) -> int | None:
    """Check if IP is rate-limited. Returns retry_after seconds or None if allowed.

    Counts failures in the window that occurred AFTER the most recent successful
    login from the same IP — so a successful login resets the failure counter
    without deleting historical rows (kept for the security log).
    """
    cutoff = time.time() - config.LOGIN_WINDOW_SECONDS
    with get_db() as conn:
        last_success = conn.execute(
            "SELECT MAX(attempted_at) FROM login_attempts WHERE ip = ? AND success = 1",
            (ip,),
        ).fetchone()[0] or 0
        floor = max(cutoff, last_success)
        row = conn.execute(
            "SELECT COUNT(*), MAX(attempted_at) FROM login_attempts "
            "WHERE ip = ? AND attempted_at > ? AND success = 0",
            (ip, floor),
        ).fetchone()
        fail_count, last_fail = row[0], row[1]
        if fail_count >= config.MAX_FAILED_ATTEMPTS and last_fail:
            blocked_until = last_fail + config.LOCKOUT_SECONDS
            remaining = int(blocked_until - time.time())
            if remaining > 0:
                return remaining
    return None


def record_login_attempt(ip: str, success: bool, username: str | None = None, user_agent: str | None = None):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO login_attempts (ip, attempted_at, success, username, user_agent) "
            "VALUES (?, ?, ?, ?, ?)",
            (ip, time.time(), 1 if success else 0, username, user_agent),
        )
        conn.commit()


def cleanup_old_login_attempts():
    """Remove login attempts older than 30 days (kept for security log display)."""
    cutoff = time.time() - 30 * 86400
    with get_db() as conn:
        conn.execute("DELETE FROM login_attempts WHERE attempted_at < ?", (cutoff,))
        conn.commit()
