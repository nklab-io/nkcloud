import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from ..auth import hash_password as pbkdf2_hash, verify_password as pbkdf2_verify
from ..database import get_db, record_audit
from ..models import ShareCreatePayload, SharePasswordPayload
from ..permissions import remap_path_for_user, check_permission
from ..services import filesystem as fs
from ..services.zip_svc import stream_zip

router = APIRouter(tags=["shares"])


def _verify_share_password(password: str, share: dict) -> bool:
    """Verify share password, supporting both old SHA256 and new PBKDF2."""
    pw_version = share.get("pw_version", 1)
    stored_hash = share["password_hash"]
    if pw_version == 1:
        # Legacy SHA256
        return hashlib.sha256(password.encode()).hexdigest() == stored_hash
    else:
        # PBKDF2
        return pbkdf2_verify(password, stored_hash)


def _check_share_valid(share: dict) -> bool:
    if share["expires_at"]:
        exp = datetime.fromisoformat(share["expires_at"]).replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > exp:
            return False
    return True


def _user(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def _client_ip(request: Request) -> str:
    for header in ("cf-connecting-ip", "x-forwarded-for", "x-real-ip"):
        raw = request.headers.get(header)
        if raw:
            return raw.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# --- Authenticated endpoints ---

@router.get("/api/shares")
def list_shares(request: Request):
    user = _user(request)
    with get_db() as conn:
        if user["role"] in ("owner", "admin"):
            rows = conn.execute("SELECT * FROM shares ORDER BY created_at DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM shares WHERE created_by = ? ORDER BY created_at DESC",
                (user["id"],),
            ).fetchall()
    return [dict(r) for r in rows]


@router.post("/api/shares")
def create_share(payload: ShareCreatePayload, request: Request):
    user = _user(request)
    path = remap_path_for_user(payload.path, user)
    check_permission(user, "read", path)

    try:
        abs_path = fs.safe_resolve(path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="Path not found")

    if payload.type not in ("file_download", "browse"):
        raise HTTPException(status_code=400, detail="Type must be 'file_download' or 'browse'")

    share_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(8)
    pw_hash = pbkdf2_hash(payload.password) if payload.password else None
    is_dir = 1 if os.path.isdir(abs_path) else 0

    with get_db() as conn:
        conn.execute(
            "INSERT INTO shares (id, token, path, is_directory, password_hash, expires_at, type, created_by, pw_version) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (share_id, token, path, is_dir, pw_hash, payload.expires_at, payload.type, user["id"], 2),
        )
        conn.commit()

    record_audit(user["id"], user["username"], "share_create", target_path=path,
                 detail=json.dumps({"share_id": share_id, "type": payload.type}),
                 ip=_client_ip(request))

    return {"id": share_id, "token": token, "url": f"/s/{token}"}


@router.delete("/api/shares/{share_id}")
def delete_share(share_id: str, request: Request):
    user = _user(request)
    with get_db() as conn:
        row = conn.execute("SELECT * FROM shares WHERE id = ?", (share_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Share not found")
        share = dict(row)
        # Owner can delete any, admin can delete any, user can only delete own
        if user["role"] == "user" and share.get("created_by") != user["id"]:
            raise HTTPException(status_code=403, detail="Permission denied")
        conn.execute("DELETE FROM shares WHERE id = ?", (share_id,))
        conn.commit()

    record_audit(user["id"], user["username"], "share_delete",
                 detail=json.dumps({"share_id": share_id}),
                 ip=_client_ip(request))

    return {"deleted": share_id}


# --- Public endpoints ---

def _get_share(token: str) -> dict:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM shares WHERE token = ?", (token,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Share not found")
    share = dict(row)
    if not _check_share_valid(share):
        raise HTTPException(status_code=410, detail="Share expired")
    return share


@router.get("/api/public/{token}")
def public_share_info(token: str, request: Request):
    share = _get_share(token)

    # browse type requires authentication
    if share.get("type") == "browse":
        from ..main import get_current_user
        user = get_current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="Login required to view this share")

    needs_password = share["password_hash"] is not None
    try:
        abs_path = fs.safe_resolve(share["path"])
    except ValueError:
        raise HTTPException(status_code=404, detail="Path not found")
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="Path not found")

    info = {
        "token": token,
        "path": share["path"],
        "is_directory": bool(share["is_directory"]),
        "needs_password": needs_password,
        "name": os.path.basename(abs_path) or "shared",
        "type": share.get("type", "file_download"),
    }

    if not needs_password:
        if share["is_directory"]:
            info["items"] = fs.list_directory(abs_path)
        else:
            st = os.stat(abs_path)
            info["size"] = st.st_size
            info["mime_type"] = fs.get_mime_type(os.path.basename(abs_path))
    return info


@router.post("/api/public/{token}/verify")
def verify_share_password(token: str, payload: SharePasswordPayload):
    share = _get_share(token)
    if not share["password_hash"]:
        return {"valid": True}
    if not _verify_share_password(payload.password, share):
        raise HTTPException(status_code=401, detail="Invalid password")

    try:
        abs_path = fs.safe_resolve(share["path"])
    except ValueError:
        raise HTTPException(status_code=404, detail="Path not found")
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="Path not found")

    info = {
        "valid": True,
        "is_directory": bool(share["is_directory"]),
        "name": os.path.basename(abs_path) or "shared",
    }
    if share["is_directory"]:
        info["items"] = fs.list_directory(abs_path)
    else:
        st = os.stat(abs_path)
        info["size"] = st.st_size
        info["mime_type"] = fs.get_mime_type(os.path.basename(abs_path))
    return info


@router.get("/api/public/{token}/download")
def public_download(token: str, request: Request, path: str = ""):
    share = _get_share(token)

    # browse type requires authentication
    if share.get("type") == "browse":
        from ..main import get_current_user
        user = get_current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="Login required")

    try:
        share_root = fs.safe_resolve(share["path"])
    except ValueError:
        raise HTTPException(status_code=404, detail="Path not found")

    if path and share["is_directory"]:
        target = os.path.realpath(os.path.join(share_root, path.lstrip("/")))
        if not target.startswith(share_root):
            raise HTTPException(status_code=400, detail="Invalid path")
        if not os.path.isfile(target):
            raise HTTPException(status_code=404, detail="File not found")
        abs_path = target
    else:
        abs_path = share_root

    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="Path not found")

    with get_db() as conn:
        conn.execute("UPDATE shares SET download_count = download_count + 1 WHERE token = ?", (token,))
        conn.commit()

    if os.path.isdir(abs_path):
        folder_name = os.path.basename(abs_path) or "shared"
        return StreamingResponse(
            stream_zip(abs_path, folder_name),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{folder_name}.zip"'},
        )
    else:
        return FileResponse(
            abs_path,
            media_type=fs.get_mime_type(os.path.basename(abs_path)),
            filename=os.path.basename(abs_path),
        )
