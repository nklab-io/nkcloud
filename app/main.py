import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .auth import hash_password, verify_password
from .database import (
    init_db, has_any_users, get_user_by_id, get_user_by_username,
    get_owner_user, record_audit, check_rate_limit, record_login_attempt,
    cleanup_old_login_attempts, cleanup_old_share_verify_attempts,
    reconcile_used_bytes, get_db,
)
from .models import LoginPayload, SetupPayload, RegisterPayload

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
INDEX_PATH = os.path.join(STATIC_DIR, "index.html")
LOGIN_PATH = os.path.join(STATIC_DIR, "login.html")
SHARED_PATH = os.path.join(STATIC_DIR, "shared.html")
SETUP_PATH = os.path.join(STATIC_DIR, "setup.html")
INVITE_PATH = os.path.join(STATIC_DIR, "invite.html")

app = FastAPI(title="NkCloud")

# Module-level setup state (set during init)
_setup_complete = False

NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers.update(NO_CACHE_HEADERS)
        return response





def _pad_base64(value: str) -> str:
    return value + "=" * (-len(value) % 4)


_TRUST_PROXY = os.getenv("NKCLOUD_TRUST_PROXY", "").lower() in ("1", "true", "yes")


def get_client_ip(request: Request) -> str:
    """Return the caller's IP.

    Only consults forwarded headers if NKCLOUD_TRUST_PROXY is set — otherwise
    an attacker hitting the app directly could spoof X-Forwarded-For to dodge
    per-IP rate limits. For single-server deploys behind NPM set the env var;
    for direct-exposure deploys leave it unset.
    """
    if _TRUST_PROXY:
        for header in ("cf-connecting-ip", "x-forwarded-for", "x-real-ip"):
            raw = request.headers.get(header)
            if raw:
                return raw.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _ensure_session_secret():
    """Ensure SESSION_SECRET is set. Auto-generate and persist if needed."""
    if config.SESSION_SECRET:
        return
    # Try to load from file
    if os.path.exists(config.SESSION_SECRET_FILE):
        with open(config.SESSION_SECRET_FILE) as f:
            config.SESSION_SECRET = f.read().strip()
        if config.SESSION_SECRET:
            return
    # Generate new secret
    config.SESSION_SECRET = secrets.token_hex(32)
    os.makedirs(os.path.dirname(config.SESSION_SECRET_FILE), exist_ok=True)
    with open(config.SESSION_SECRET_FILE, "w") as f:
        f.write(config.SESSION_SECRET)


def create_session_token(user_id: str, username: str) -> str:
    payload = {
        "exp": int(time.time()) + config.SESSION_TTL_SECONDS,
        "nonce": secrets.token_urlsafe(8),
        "uid": user_id,
        "user": username,
    }
    payload_raw = json.dumps(payload, separators=(",", ":")).encode()
    payload_b64 = base64.urlsafe_b64encode(payload_raw).decode().rstrip("=")
    signature = hmac.new(config.SESSION_SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{signature}"


def decode_session_token(request: Request) -> dict | None:
    """Decode and validate session token. Returns payload dict or None."""
    token = request.cookies.get(config.SESSION_COOKIE)
    if not token or "." not in token:
        return None
    payload_b64, signature = token.rsplit(".", 1)
    expected = hmac.new(config.SESSION_SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload_raw = base64.urlsafe_b64decode(_pad_base64(payload_b64)).decode()
        payload = json.loads(payload_raw)
    except (ValueError, json.JSONDecodeError):
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload


def get_current_user(request: Request) -> dict | None:
    """Get the authenticated user from session. Returns user dict or None."""
    # Check if already resolved in this request
    if hasattr(request.state, "user") and request.state.user is not None:
        return request.state.user

    payload = decode_session_token(request)
    if not payload or "uid" not in payload:
        return None

    user = get_user_by_id(payload["uid"])
    if not user or user["is_disabled"]:
        return None
    return user


def _is_secure_request(request: Request) -> bool:
    """Detect HTTPS: direct TLS, reverse-proxy header, or explicit env override."""
    forced = os.getenv("NKCLOUD_COOKIE_SECURE", "").lower()
    if forced in ("1", "true", "yes"):
        return True
    if forced in ("0", "false", "no"):
        return False
    if request.url.scheme == "https":
        return True
    proto = request.headers.get("x-forwarded-proto", "").lower()
    return proto == "https"


def set_session_cookie(response: JSONResponse, user_id: str, username: str, request: Request):
    response.set_cookie(
        key=config.SESSION_COOKIE,
        value=create_session_token(user_id, username),
        max_age=config.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=_is_secure_request(request),
        path="/",
    )


def set_csrf_cookie(response: JSONResponse, request: Request):
    csrf_token = secrets.token_urlsafe(32)
    response.set_cookie(
        key=config.CSRF_COOKIE,
        value=csrf_token,
        max_age=config.SESSION_TTL_SECONDS,
        httponly=False,  # JS needs to read this
        samesite="lax",
        secure=_is_secure_request(request),
        path="/",
    )


def clear_session_cookie(response: JSONResponse):
    response.delete_cookie(key=config.SESSION_COOKIE, path="/")
    response.delete_cookie(key=config.CSRF_COOKIE, path="/")



SETUP_PREFIXES = ("/setup", "/api/setup")
PUBLIC_PREFIXES = (
    "/login", "/api/login", "/api/session",
    "/s/", "/api/public/",
    "/invite/", "/api/invite/",
    "/static/",
)


def _is_setup_path(path: str) -> bool:
    return any(path.startswith(p) or path == p.rstrip("/") for p in SETUP_PREFIXES) or path.startswith("/static/")


def _is_public_path(path: str) -> bool:
    return any(path.startswith(p) or path == p.rstrip("/") for p in PUBLIC_PREFIXES)


def _is_csrf_exempt(path: str) -> bool:
    return path in ("/api/login", "/api/setup") or path.startswith("/api/invite/") or path.startswith("/api/public/")



@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    global _setup_complete
    path = request.url.path

    # Setup mode: redirect everything to /setup except setup paths
    if not _setup_complete:
        if not _is_setup_path(path):
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Setup required"}, status_code=503)
            return RedirectResponse(url="/setup", status_code=303)
        return await call_next(request)

    # CSRF check for mutating requests
    if request.method in ("POST", "PUT", "DELETE") and not _is_csrf_exempt(path):
        csrf_cookie = request.cookies.get(config.CSRF_COOKIE, "")
        csrf_header = request.headers.get("x-csrf-token", "")
        if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
            return JSONResponse({"detail": "CSRF token invalid"}, status_code=403)

    # Public paths don't need auth
    if _is_public_path(path) or _is_setup_path(path):
        return await call_next(request)

    # Authenticate
    user = get_current_user(request)
    if user:
        request.state.user = user
        return await call_next(request)

    # Not authenticated
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Authentication required"}, status_code=401)
    return RedirectResponse(url="/login", status_code=303)



init_db()
_ensure_session_secret()
_setup_complete = has_any_users()
os.makedirs(config.THUMB_DIR, exist_ok=True)
os.makedirs(config.CHUNK_DIR, exist_ok=True)
os.makedirs(os.path.join(config.FILE_ROOT, config.HOMES_DIR), exist_ok=True)

# Cleanup old rate limit entries
cleanup_old_login_attempts()
cleanup_old_share_verify_attempts()

# Drift-safety net: recompute used_bytes from disk on boot. Hot paths update
# via deltas (routers.files._adjust_used_bytes); this catches skew from
# crashes, manual edits, etc.
try:
    reconcile_used_bytes()
except Exception:
    pass

# Start WebDAV server on port 8001 unless tests or constrained deploys disable it.
_webdav_server = None
if os.getenv("NKCLOUD_DISABLE_WEBDAV", "").lower() not in ("1", "true", "yes"):
    from .webdav import start_webdav_server
    _webdav_server = start_webdav_server()



@app.get("/setup")
def setup_page():
    if _setup_complete:
        return RedirectResponse(url="/", status_code=303)
    return FileResponse(SETUP_PATH, headers=NO_CACHE_HEADERS)


@app.post("/api/setup")
def setup(payload: SetupPayload, request: Request):
    global _setup_complete
    if _setup_complete:
        raise HTTPException(status_code=400, detail="Setup already completed")

    username = payload.username.strip()
    if not username or not re.match(r'^[a-zA-Z0-9_-]{2,32}$', username):
        raise HTTPException(status_code=400, detail="Invalid username (2-32 chars, alphanumeric/_/-)")
    if len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    user_id = str(uuid.uuid4())
    pw_hash = hash_password(payload.password)

    with get_db() as conn:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, role, quota_bytes) VALUES (?,?,?,?,?)",
            (user_id, username, pw_hash, "owner", 0),
        )
        conn.commit()

    # Create homes directory
    os.makedirs(os.path.join(config.FILE_ROOT, config.HOMES_DIR), exist_ok=True)

    _setup_complete = True

    record_audit(user_id, username, "setup", ip=get_client_ip(request))

    response = JSONResponse({"ok": True})
    set_session_cookie(response, user_id, username, request)
    set_csrf_cookie(response, request)
    return response



@app.get("/")
def index_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return FileResponse(INDEX_PATH, headers=NO_CACHE_HEADERS)


@app.get("/login")
def login_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/", status_code=303)
    return FileResponse(LOGIN_PATH, headers=NO_CACHE_HEADERS)


@app.post("/api/login")
def login(payload: LoginPayload, request: Request):
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent", "")[:300]

    # Rate limit check — dual axis (per-IP and per-username)
    retry_after = check_rate_limit(client_ip, username=payload.username)
    if retry_after:
        record_login_attempt(client_ip, success=False, username=payload.username, user_agent=user_agent)
        raise HTTPException(status_code=429, detail=f"Too many failed attempts. Retry in {retry_after}s")

    # Find user
    user = get_user_by_username(payload.username)
    if not user or not verify_password(payload.password, user["password_hash"]):
        record_login_attempt(client_ip, success=False, username=payload.username, user_agent=user_agent)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if user["is_disabled"]:
        record_login_attempt(client_ip, success=False, username=payload.username, user_agent=user_agent)
        raise HTTPException(status_code=401, detail="Account disabled")

    record_login_attempt(client_ip, success=True, username=user["username"], user_agent=user_agent)
    record_audit(user["id"], user["username"], "login", ip=client_ip)

    response = JSONResponse({"ok": True})
    set_session_cookie(response, user["id"], user["username"], request)
    set_csrf_cookie(response, request)
    return response


@app.post("/api/logout")
def logout(request: Request):
    user = get_current_user(request)
    if user:
        record_audit(user["id"], user["username"], "logout", ip=get_client_ip(request))
    response = JSONResponse({"ok": True})
    clear_session_cookie(response)
    return response


@app.get("/api/session")
def session_status(request: Request):
    user = get_current_user(request)
    if user:
        return {
            "authenticated": True,
            "user": {
                "id": user["id"],
                "username": user["username"],
                "role": user["role"],
            },
        }
    return {"authenticated": False}



@app.get("/invite/{token}")
def invite_page(token: str):
    return FileResponse(INVITE_PATH, headers=NO_CACHE_HEADERS)


@app.get("/api/invite/{token}")
def invite_info(token: str):
    """Check if an invite token is valid."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM invites WHERE token = ?", (token,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Invite not found")
    invite = dict(row)
    if invite["used_at"]:
        raise HTTPException(status_code=410, detail="Invite already used")
    if invite["expires_at"]:
        from datetime import datetime, timezone
        exp = datetime.fromisoformat(invite["expires_at"]).replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > exp:
            raise HTTPException(status_code=410, detail="Invite expired")
    return {"valid": True}


@app.post("/api/invite/{token}")
def register_via_invite(token: str, payload: RegisterPayload, request: Request):
    """Register a new user via invite token."""
    # Validate invite
    with get_db() as conn:
        row = conn.execute("SELECT * FROM invites WHERE token = ?", (token,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Invite not found")
    invite = dict(row)
    if invite["used_at"]:
        raise HTTPException(status_code=410, detail="Invite already used")
    if invite["expires_at"]:
        from datetime import datetime, timezone
        exp = datetime.fromisoformat(invite["expires_at"]).replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > exp:
            raise HTTPException(status_code=410, detail="Invite expired")

    # Validate username
    username = payload.username.strip()
    if not username or not re.match(r'^[a-zA-Z0-9_-]{2,32}$', username):
        raise HTTPException(status_code=400, detail="Invalid username (2-32 chars, alphanumeric/_/-)")
    if len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    # Reserved names
    if username.lower() in ("admin", "root", "system", config.HOMES_DIR):
        raise HTTPException(status_code=400, detail="Username reserved")

    # Check uniqueness
    if get_user_by_username(username):
        raise HTTPException(status_code=409, detail="Username already taken")

    # Create user
    user_id = str(uuid.uuid4())
    pw_hash = hash_password(payload.password)

    with get_db() as conn:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, role, quota_bytes) VALUES (?,?,?,?,?)",
            (user_id, username, pw_hash, "user", config.DEFAULT_QUOTA_BYTES),
        )
        # Mark invite as used
        from datetime import datetime, timezone
        conn.execute(
            "UPDATE invites SET used_at = ?, used_by = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), user_id, invite["id"]),
        )
        conn.commit()

    # Create home directory
    home_dir = os.path.join(config.FILE_ROOT, config.HOMES_DIR, username)
    os.makedirs(home_dir, exist_ok=True)

    client_ip = get_client_ip(request)
    record_audit(user_id, username, "register", ip=client_ip,
                 detail=json.dumps({"invite_id": invite["id"]}))

    # Auto-login
    response = JSONResponse({"ok": True, "username": username})
    set_session_cookie(response, user_id, username, request)
    set_csrf_cookie(response, request)
    return response



from .routers import files, shares, thumbnails, search  # noqa: E402

app.include_router(files.router, prefix="/api")
app.include_router(shares.router)
app.include_router(thumbnails.router, prefix="/api")
app.include_router(search.router, prefix="/api")

# User management router
from .routers import users  # noqa: E402
app.include_router(users.router)

# Audit router
from .routers import audit  # noqa: E402
app.include_router(audit.router)

# Security router (login attempts log)
from .routers import security  # noqa: E402
app.include_router(security.router)



@app.get("/s/{token}")
def share_page(token: str):
    return FileResponse(SHARED_PATH, headers=NO_CACHE_HEADERS)



app.mount("/static", NoCacheStaticFiles(directory=STATIC_DIR), name="static")
