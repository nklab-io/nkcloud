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

        # --- Chunked upload sessions (server-tracked, per-user) ---
        conn.execute("""
            CREATE TABLE IF NOT EXISTS upload_sessions (
                upload_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                dest_rel_path TEXT NOT NULL,
                filename TEXT NOT NULL,
                total_bytes INTEGER NOT NULL,
                total_chunks INTEGER NOT NULL,
                received_bytes INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_upload_sessions_user ON upload_sessions(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_upload_sessions_expires ON upload_sessions(expires_at)")

        # --- Share verify throttle (separate axis from login_attempts) ---
        conn.execute("""
            CREATE TABLE IF NOT EXISTS share_verify_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip TEXT NOT NULL,
                token TEXT NOT NULL,
                attempted_at REAL NOT NULL,
                success INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_share_verify_ip ON share_verify_attempts(ip)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_share_verify_time ON share_verify_attempts(attempted_at)")

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


def check_rate_limit(ip: str, username: str | None = None) -> int | None:
    """Dual-axis rate limit: per-IP and per-username.

    Previously a single axis (IP), and any successful login from that IP reset
    the failure count — so an attacker with one valid account could brute-
    force another by interleaving a success every few tries. Now:
      - Per-IP: floor by the window cutoff only; successes do NOT reset.
      - Per-username: also floor by the window cutoff; a successful login for
        user A does not clear user B's failures.

    Returns the max retry-after across both axes, or None if neither blocks.
    """
    cutoff = time.time() - config.LOGIN_WINDOW_SECONDS
    retry_candidates = []
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*), MAX(attempted_at) FROM login_attempts "
            "WHERE ip = ? AND attempted_at > ? AND success = 0",
            (ip, cutoff),
        ).fetchone()
        fail_ip, last_fail_ip = row[0], row[1]
        if fail_ip >= config.MAX_FAILED_ATTEMPTS_IP and last_fail_ip:
            remaining = int(last_fail_ip + config.LOCKOUT_SECONDS - time.time())
            if remaining > 0:
                retry_candidates.append(remaining)

        if username:
            row_u = conn.execute(
                "SELECT COUNT(*), MAX(attempted_at) FROM login_attempts "
                "WHERE username = ? AND attempted_at > ? AND success = 0",
                (username, cutoff),
            ).fetchone()
            fail_u, last_fail_u = row_u[0], row_u[1]
            if fail_u >= config.MAX_FAILED_ATTEMPTS and last_fail_u:
                remaining = int(last_fail_u + config.LOCKOUT_SECONDS - time.time())
                if remaining > 0:
                    retry_candidates.append(remaining)

    return max(retry_candidates) if retry_candidates else None


def check_share_verify_rate_limit(ip: str) -> int | None:
    """Per-IP throttle for anonymous /api/public/{token}/verify attempts."""
    cutoff = time.time() - config.SHARE_VERIFY_WINDOW_SECONDS
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*), MAX(attempted_at) FROM share_verify_attempts "
            "WHERE ip = ? AND attempted_at > ? AND success = 0",
            (ip, cutoff),
        ).fetchone()
        fail_count, last_fail = row[0], row[1]
    if fail_count >= config.SHARE_VERIFY_MAX_ATTEMPTS and last_fail:
        remaining = int(last_fail + config.SHARE_VERIFY_LOCKOUT_SECONDS - time.time())
        if remaining > 0:
            return remaining
    return None


def record_share_verify_attempt(ip: str, token: str, success: bool):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO share_verify_attempts (ip, token, attempted_at, success) VALUES (?,?,?,?)",
            (ip, token, time.time(), 1 if success else 0),
        )
        conn.commit()


def cleanup_old_share_verify_attempts():
    cutoff = time.time() - 30 * 86400
    with get_db() as conn:
        conn.execute("DELETE FROM share_verify_attempts WHERE attempted_at < ?", (cutoff,))
        conn.commit()


def reconcile_used_bytes():
    """Recompute used_bytes for every non-owner user by walking their home dir.

    Called once on startup as a drift-safety net. Hot paths update via deltas
    (see routers.files._adjust_used_bytes); this catches any accumulated skew
    from crashes, manual filesystem edits, etc.
    """
    import os as _os
    from .services import filesystem as fs
    with get_db() as conn:
        users = conn.execute("SELECT id, username, role FROM users WHERE role != 'owner'").fetchall()
    for u in users:
        home_dir = _os.path.join(config.FILE_ROOT, config.HOMES_DIR, u["username"])
        if not _os.path.isdir(home_dir):
            continue
        size = fs.get_directory_size(home_dir)
        with get_db() as conn:
            conn.execute("UPDATE users SET used_bytes = ? WHERE id = ?", (size, u["id"]))
            conn.commit()


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
