import json
import os
import secrets
import shutil
import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Request

from .. import config
from ..auth import hash_password
from ..database import get_db, get_user_by_id, get_owner_user, record_audit
from ..models import InviteCreatePayload, UserUpdatePayload, UserDeletePayload

router = APIRouter(tags=["users"])


def _require_user(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def _require_owner(request: Request) -> dict:
    user = _require_user(request)
    if user["role"] != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    return user


def _require_owner_or_admin(request: Request) -> dict:
    user = _require_user(request)
    if user["role"] not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Owner or admin only")
    return user


def _get_client_ip(request: Request) -> str:
    if os.getenv("NKCLOUD_TRUST_PROXY", "").lower() in ("1", "true", "yes"):
        for header in ("cf-connecting-ip", "x-forwarded-for", "x-real-ip"):
            raw = request.headers.get(header)
            if raw:
                return raw.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# --- User management ---

@router.get("/api/users")
def list_users(request: Request):
    _require_owner(request)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, username, role, quota_bytes, used_bytes, is_disabled, created_at, updated_at "
            "FROM users ORDER BY created_at"
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/api/users/me")
def get_me(request: Request):
    user = _require_user(request)
    last_login_at = None
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT attempted_at FROM login_attempts "
                "WHERE username = ? AND success = 1 "
                "ORDER BY attempted_at DESC LIMIT 1",
                (user["username"],)
            ).fetchone()
        if row:
            last_login_at = row["attempted_at"]
    except Exception:
        pass
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "quota_bytes": user["quota_bytes"],
        "used_bytes": user["used_bytes"],
        "current_ip": _get_client_ip(request),
        "last_login_at": last_login_at,
    }


@router.put("/api/users/{user_id}")
def update_user(user_id: str, payload: UserUpdatePayload, request: Request):
    caller = _require_owner(request)
    if user_id == caller["id"]:
        raise HTTPException(status_code=400, detail="Cannot modify own account via this endpoint")

    target = get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    updates = {}
    if payload.role is not None:
        if payload.role not in ("admin", "user"):
            raise HTTPException(status_code=400, detail="Role must be 'admin' or 'user'")
        if target["role"] == "owner":
            raise HTTPException(status_code=400, detail="Cannot change owner role")
        updates["role"] = payload.role

    if payload.quota_bytes is not None:
        if payload.quota_bytes < 0:
            raise HTTPException(status_code=400, detail="Quota must be >= 0")
        updates["quota_bytes"] = payload.quota_bytes

    if payload.is_disabled is not None:
        if target["role"] == "owner":
            raise HTTPException(status_code=400, detail="Cannot disable owner")
        updates["is_disabled"] = 1 if payload.is_disabled else 0

    if not updates:
        raise HTTPException(status_code=400, detail="No changes specified")

    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [user_id]

    with get_db() as conn:
        conn.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)
        conn.commit()

    record_audit(caller["id"], caller["username"], "user_update",
                 detail=json.dumps({"target": target["username"], "changes": {k: v for k, v in updates.items() if k != "updated_at"}}),
                 ip=_get_client_ip(request))

    return {"ok": True}


@router.delete("/api/users/{user_id}")
def delete_user(user_id: str, request: Request, delete_files: bool = False):
    caller = _require_owner(request)
    if user_id == caller["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete own account")

    target = get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target["role"] == "owner":
        raise HTTPException(status_code=400, detail="Cannot delete owner")

    with get_db() as conn:
        # Delete user's shares
        conn.execute("DELETE FROM shares WHERE created_by = ?", (user_id,))
        # Delete user
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()

    # Handle home directory
    home_dir = os.path.join(config.FILE_ROOT, config.HOMES_DIR, target["username"])
    if delete_files and os.path.isdir(home_dir):
        shutil.rmtree(home_dir)

    record_audit(caller["id"], caller["username"], "user_delete",
                 detail=json.dumps({"target": target["username"], "files_deleted": delete_files}),
                 ip=_get_client_ip(request))

    return {"ok": True}


# --- Invites ---

@router.post("/api/invites")
def create_invite(payload: InviteCreatePayload, request: Request):
    caller = _require_owner_or_admin(request)

    invite_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(16)
    expires_at = None
    if payload.expires_hours and payload.expires_hours > 0:
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=payload.expires_hours)).isoformat()

    with get_db() as conn:
        conn.execute(
            "INSERT INTO invites (id, token, created_by, expires_at) VALUES (?,?,?,?)",
            (invite_id, token, caller["id"], expires_at),
        )
        conn.commit()

    record_audit(caller["id"], caller["username"], "invite_create",
                 detail=json.dumps({"invite_id": invite_id}),
                 ip=_get_client_ip(request))

    return {"id": invite_id, "token": token, "url": f"/invite/{token}"}


@router.get("/api/invites")
def list_invites(request: Request):
    caller = _require_owner_or_admin(request)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT i.*, u.username as created_by_name FROM invites i "
            "LEFT JOIN users u ON i.created_by = u.id "
            "ORDER BY i.created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


@router.delete("/api/invites/{invite_id}")
def delete_invite(invite_id: str, request: Request):
    caller = _require_owner_or_admin(request)
    with get_db() as conn:
        result = conn.execute("DELETE FROM invites WHERE id = ?", (invite_id,))
        conn.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Invite not found")

    record_audit(caller["id"], caller["username"], "invite_delete",
                 detail=json.dumps({"invite_id": invite_id}),
                 ip=_get_client_ip(request))

    return {"ok": True}
