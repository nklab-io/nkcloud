"""Soft-delete / trash service.

Layout (per user):
    <home>/.trash/
        .index.json          # metadata map
        <entry_id>           # actual file or dir, renamed

Regular users: home = /_homes/{username}/
Owner:         home = / (FILE_ROOT itself)
Admin:         their own /_homes/{admin_name}/ (admins have a home)

Trash still counts toward quota because it lives inside the user's home.
Entries are purged after TRASH_RETENTION_DAYS.
"""

import fcntl
import json
import os
import shutil
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional

from .. import config
from . import filesystem as fs


INDEX_NAME = ".index.json"
LOCK_NAME = ".lock"


@contextmanager
def _index_lock(trash_dir: str):
    """Process-level exclusive lock on the trash dir's index. Serialises
    concurrent delete/restore/purge so the index file isn't clobbered."""
    os.makedirs(trash_dir, exist_ok=True)
    lock_path = os.path.join(trash_dir, LOCK_NAME)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def trash_dir_for_user(user: dict) -> str:
    """Absolute path to the user's trash directory."""
    if user["role"] == "owner":
        base = os.path.realpath(config.FILE_ROOT)
    else:
        base = os.path.join(os.path.realpath(config.FILE_ROOT), config.HOMES_DIR, user["username"])
    return os.path.join(base, config.TRASH_DIR)


def _read_index(trash_dir: str) -> dict:
    idx_path = os.path.join(trash_dir, INDEX_NAME)
    if not os.path.exists(idx_path):
        return {}
    try:
        with open(idx_path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _write_index(trash_dir: str, data: dict):
    os.makedirs(trash_dir, exist_ok=True)
    idx_path = os.path.join(trash_dir, INDEX_NAME)
    tmp = idx_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, idx_path)


def move_to_trash(abs_src: str, orig_rel_path: str, user: dict) -> str:
    """Move abs_src into the user's trash. Returns entry id."""
    trash_dir = trash_dir_for_user(user)
    with _index_lock(trash_dir):
        entry_id = uuid.uuid4().hex
        dest = os.path.join(trash_dir, entry_id)
        shutil.move(abs_src, dest)

        try:
            if os.path.isdir(dest):
                size = fs.get_directory_size(dest)
                is_dir = True
            else:
                size = os.path.getsize(dest)
                is_dir = False
        except OSError:
            size = 0
            is_dir = os.path.isdir(dest)

        index = _read_index(trash_dir)
        index[entry_id] = {
            "orig_path": orig_rel_path,
            "orig_name": os.path.basename(orig_rel_path.rstrip("/")) or orig_rel_path,
            "deleted_at": datetime.now(timezone.utc).isoformat(),
            "size": size,
            "is_dir": is_dir,
        }
        _write_index(trash_dir, index)
        return entry_id


def list_trash(user: dict, display_prefix: Optional[str] = None) -> list[dict]:
    """Return trash entries as list of dicts, newest first.

    display_prefix: if set, strip from orig_path to show user-relative paths
    (used for regular users so they see '/foo.txt' not '/_homes/alice/foo.txt').
    """
    trash_dir = trash_dir_for_user(user)
    index = _read_index(trash_dir)
    retention = timedelta(days=config.TRASH_RETENTION_DAYS)
    now = datetime.now(timezone.utc)
    entries = []
    for eid, meta in index.items():
        try:
            deleted_at = datetime.fromisoformat(meta["deleted_at"])
        except (KeyError, ValueError):
            continue
        expires_at = deleted_at + retention
        orig_path = meta.get("orig_path", "")
        display_path = orig_path
        if display_prefix and display_path.startswith(display_prefix):
            display_path = display_path[len(display_prefix):] or "/"
        entries.append({
            "id": eid,
            "orig_path": display_path,
            "orig_name": meta.get("orig_name", eid),
            "deleted_at": meta["deleted_at"],
            "expires_at": expires_at.isoformat(),
            "days_left": max(0, (expires_at - now).days),
            "size": meta.get("size", 0),
            "is_dir": meta.get("is_dir", False),
        })
    entries.sort(key=lambda e: e["deleted_at"], reverse=True)
    return entries


def purge_expired(user: dict) -> int:
    """Delete entries past retention. Returns count purged."""
    trash_dir = trash_dir_for_user(user)
    if not os.path.isdir(trash_dir):
        return 0
    with _index_lock(trash_dir):
        index = _read_index(trash_dir)
        retention = timedelta(days=config.TRASH_RETENTION_DAYS)
        now = datetime.now(timezone.utc)
        removed = []
        for eid, meta in list(index.items()):
            try:
                deleted_at = datetime.fromisoformat(meta["deleted_at"])
            except (KeyError, ValueError):
                continue
            if now - deleted_at >= retention:
                _remove_entry(trash_dir, eid)
                removed.append(eid)
        if removed:
            for eid in removed:
                index.pop(eid, None)
            _write_index(trash_dir, index)
        return len(removed)


def _remove_entry(trash_dir: str, entry_id: str):
    """Remove actual file/dir for an entry id, ignoring missing."""
    target = os.path.join(trash_dir, entry_id)
    if os.path.isdir(target):
        shutil.rmtree(target, ignore_errors=True)
    elif os.path.exists(target):
        try:
            os.remove(target)
        except OSError:
            pass


def purge_ids(user: dict, entry_ids: list[str]) -> list[str]:
    """Permanently delete specific entries. Returns ids removed."""
    trash_dir = trash_dir_for_user(user)
    if not os.path.isdir(trash_dir):
        return []
    with _index_lock(trash_dir):
        index = _read_index(trash_dir)
        removed = []
        for eid in entry_ids:
            if eid in index:
                _remove_entry(trash_dir, eid)
                index.pop(eid, None)
                removed.append(eid)
        if removed:
            _write_index(trash_dir, index)
        return removed


def empty_trash(user: dict) -> int:
    """Empty entire trash. Returns count removed."""
    trash_dir = trash_dir_for_user(user)
    if not os.path.isdir(trash_dir):
        return 0
    with _index_lock(trash_dir):
        index = _read_index(trash_dir)
        count = len(index)
        for eid in list(index.keys()):
            _remove_entry(trash_dir, eid)
        _write_index(trash_dir, {})
        return count


def restore_ids(user: dict, entry_ids: list[str]) -> dict:
    """Restore entries back to their original paths.

    Returns {restored: [ids], failed: [{id, reason}]}.
    If orig location is occupied, appends ' (restored N)' suffix.
    Re-checks write permission on the destination before restoring.
    """
    # Imported lazily to avoid circular dep with permissions module.
    from ..permissions import can_write

    trash_dir = trash_dir_for_user(user)
    if not os.path.isdir(trash_dir):
        return {"restored": [], "failed": [{"id": i, "reason": "no_trash"} for i in entry_ids]}
    with _index_lock(trash_dir):
        index = _read_index(trash_dir)
        restored = []
        failed = []
        for eid in entry_ids:
            if eid not in index:
                failed.append({"id": eid, "reason": "not_found"})
                continue
            meta = index[eid]
            src = os.path.join(trash_dir, eid)
            if not os.path.exists(src):
                index.pop(eid, None)
                failed.append({"id": eid, "reason": "missing"})
                continue
            orig_path = meta.get("orig_path", "")
            # Re-check that this user is allowed to write the destination.
            # Defends against tampered index.json or post-deletion role changes.
            if not can_write(user, orig_path):
                failed.append({"id": eid, "reason": "forbidden"})
                continue
            try:
                abs_target = fs.safe_resolve(orig_path)
            except ValueError:
                failed.append({"id": eid, "reason": "invalid_path"})
                continue
            # Ensure parent exists
            parent = os.path.dirname(abs_target)
            if not os.path.isdir(parent):
                try:
                    os.makedirs(parent, exist_ok=True)
                except OSError:
                    failed.append({"id": eid, "reason": "parent_missing"})
                    continue
            # Handle collision
            final_target = abs_target
            if os.path.exists(final_target):
                base, ext = os.path.splitext(abs_target)
                counter = 1
                while os.path.exists(final_target):
                    final_target = f"{base} (restored {counter}){ext}"
                    counter += 1
            try:
                shutil.move(src, final_target)
            except (OSError, shutil.Error) as e:
                failed.append({"id": eid, "reason": str(e)})
                continue
            restored.append(eid)
            index.pop(eid, None)
        _write_index(trash_dir, index)
        return {"restored": restored, "failed": failed}
