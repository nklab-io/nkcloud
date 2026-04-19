import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from .. import config
from ..auth import hash_password as pbkdf2_hash, verify_password as pbkdf2_verify
from ..database import (
    get_db, record_audit,
    check_share_verify_rate_limit, record_share_verify_attempt,
)
from ..models import ShareCreatePayload, SharePasswordPayload
from ..permissions import resolve_and_authorize
from ..services import filesystem as fs
from ..services.zip_svc import stream_zip

router = APIRouter(tags=["shares"])

SHARE_ACCESS_TTL_SECONDS = 60 * 60 * 24  # 24h after /verify success


def _verify_share_password(password: str, share: dict) -> bool:
    """Verify share password, supporting both old SHA256 and new PBKDF2."""
    pw_version = share.get("pw_version", 1)
    stored_hash = share["password_hash"]
    if pw_version == 1:
        # Legacy SHA256 — use constant-time comparison to avoid timing leaks
        legacy = hashlib.sha256(password.encode()).hexdigest()
        return hmac.compare_digest(legacy, stored_hash)
    else:
        return pbkdf2_verify(password, stored_hash)


def _share_cookie_name(token: str) -> str:
    # token is secrets.token_urlsafe() → safe cookie-name chars
    return f"nkcloud_share_{token}"


def _sign_share_access(token: str) -> str:
    """Issue an HMAC-signed bearer bound to a specific share token."""
    exp = int(time.time()) + SHARE_ACCESS_TTL_SECONDS
    payload = f"{token}.{exp}"
    sig = hmac.new(config.SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _verify_share_access(request: Request, token: str) -> bool:
    raw = request.cookies.get(_share_cookie_name(token))
    if not raw:
        return False
    try:
        cookie_token, exp_s, sig = raw.rsplit(".", 2)
    except ValueError:
        return False
    if cookie_token != token:
        return False
    expected = hmac.new(
        config.SESSION_SECRET.encode(),
        f"{cookie_token}.{exp_s}".encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        if int(exp_s) < int(time.time()):
            return False
    except ValueError:
        return False
    return True


def _is_secure_request(request: Request) -> bool:
    forced = os.getenv("NKCLOUD_COOKIE_SECURE", "").lower()
    if forced in ("1", "true", "yes"):
        return True
    if forced in ("0", "false", "no"):
        return False
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").lower() == "https"


def _set_share_cookie(response, token: str, request: Request):
    response.set_cookie(
        key=_share_cookie_name(token),
        value=_sign_share_access(token),
        max_age=SHARE_ACCESS_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=_is_secure_request(request),
        path=f"/api/public/{token}",
    )


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
    if os.getenv("NKCLOUD_TRUST_PROXY", "").lower() in ("1", "true", "yes"):
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
    abs_path, canonical_rel = resolve_and_authorize(user, payload.path, "read")
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="Path not found")

    if payload.type not in ("file_download", "browse"):
        raise HTTPException(status_code=400, detail="Type must be 'file_download' or 'browse'")

    share_id = str(uuid.uuid4())
    # 128-bit token. Collides at ~2^64 shares — irrelevant for this workload,
    # but removes any lingering concern about brute-force guessing.
    token = secrets.token_urlsafe(16)
    pw_hash = pbkdf2_hash(payload.password) if payload.password else None
    is_dir = 1 if os.path.isdir(abs_path) else 0

    with get_db() as conn:
        conn.execute(
            "INSERT INTO shares (id, token, path, is_directory, password_hash, expires_at, type, created_by, pw_version) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (share_id, token, canonical_rel, is_dir, pw_hash, payload.expires_at, payload.type, user["id"], 2),
        )
        conn.commit()

    record_audit(user["id"], user["username"], "share_create", target_path=canonical_rel,
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


def _public_item(entry: dict) -> dict:
    """Strip internal fs path from a listing entry before returning to the public.

    Internal paths like /_homes/alice/foo.txt shouldn't leak to anonymous
    callers of /api/public/*. The shared.html UI only uses `name` + `size`
    anyway; downloads are keyed by filename via ?path=.
    """
    return {
        "name": entry.get("name"),
        "is_dir": entry.get("is_dir", False),
        "size": entry.get("size", 0),
        "modified": entry.get("modified"),
        "mime_type": entry.get("mime_type", ""),
        "has_thumb": entry.get("has_thumb", False),
    }


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
        "is_directory": bool(share["is_directory"]),
        "needs_password": needs_password,
        "name": os.path.basename(abs_path) or "shared",
        "type": share.get("type", "file_download"),
    }

    if not needs_password:
        if share["is_directory"]:
            info["items"] = [_public_item(e) for e in fs.list_directory(abs_path)]
        else:
            st = os.stat(abs_path)
            info["size"] = st.st_size
            info["mime_type"] = fs.get_mime_type(os.path.basename(abs_path))
    return info


@router.post("/api/public/{token}/verify")
def verify_share_password(token: str, payload: SharePasswordPayload, request: Request):
    ip = _client_ip(request)
    # Anonymous throttle — independent from login rate limit so one channel
    # doesn't starve the other.
    retry = check_share_verify_rate_limit(ip)
    if retry:
        raise HTTPException(status_code=429, detail=f"Too many attempts. Retry in {retry}s")

    share = _get_share(token)
    if not share["password_hash"]:
        record_share_verify_attempt(ip, token, success=True)
        response = JSONResponse({"valid": True})
        _set_share_cookie(response, token, request)
        return response
    if not _verify_share_password(payload.password, share):
        record_share_verify_attempt(ip, token, success=False)
        raise HTTPException(status_code=401, detail="Invalid password")

    try:
        abs_path = fs.safe_resolve(share["path"])
    except ValueError:
        raise HTTPException(status_code=404, detail="Path not found")
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="Path not found")

    record_share_verify_attempt(ip, token, success=True)
    info = {
        "valid": True,
        "is_directory": bool(share["is_directory"]),
        "name": os.path.basename(abs_path) or "shared",
    }
    if share["is_directory"]:
        info["items"] = [_public_item(e) for e in fs.list_directory(abs_path)]
    else:
        st = os.stat(abs_path)
        info["size"] = st.st_size
        info["mime_type"] = fs.get_mime_type(os.path.basename(abs_path))

    response = JSONResponse(info)
    _set_share_cookie(response, token, request)
    return response


@router.get("/api/public/{token}/download")
def public_download(token: str, request: Request, path: str = ""):
    share = _get_share(token)

    # browse type requires authentication
    if share.get("type") == "browse":
        from ..main import get_current_user
        user = get_current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="Login required")

    # Password-protected shares: require signed cookie issued by /verify
    if share["password_hash"] and not _verify_share_access(request, token):
        raise HTTPException(status_code=401, detail="Share password required")

    try:
        share_root = fs.safe_resolve(share["path"])
    except ValueError:
        raise HTTPException(status_code=404, detail="Path not found")

    if path and share["is_directory"]:
        target = os.path.realpath(os.path.join(share_root, path.lstrip("/")))
        # Canonical containment: target must be share_root itself or strictly inside.
        # startswith alone is vulnerable to prefix collisions (/data/foo vs /data/foobar).
        if target != share_root and not target.startswith(share_root + os.sep):
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
