import json
import os
import shutil
import time
import uuid

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse

from ..models import MkdirPayload, RenamePayload, MovePayload, DeletePayload, TrashPayload
from ..services import filesystem as fs
from ..services import trash_svc
from ..services.zip_svc import stream_zip, stream_zip_entries
from ..permissions import (
    resolve_and_authorize, resolve_parent_and_authorize,
)
from ..database import record_audit, get_db
from .. import config

router = APIRouter(tags=["files"])


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


def _adjust_used_bytes(user_id: str, delta: int):
    """Apply a signed delta to used_bytes. Clamps to >=0.

    Replaces the old full-tree recompute on every mutation. A background job
    (see database.reconcile_used_bytes) re-syncs for drift; all hot paths
    just tick this counter.
    """
    if delta == 0:
        return
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET used_bytes = MAX(0, used_bytes + ?) WHERE id = ?",
            (delta, user_id),
        )
        conn.commit()


# --- Upload session bookkeeping (chunk DoS guard) ---

def _purge_expired_upload_sessions():
    """Drop expired sessions and reclaim their chunk directories.

    Called lazily from /upload/init. Without this a client could reserve
    quota, abandon the session, and pin the quota forever.
    """
    now = time.time()
    with get_db() as conn:
        expired = conn.execute(
            "SELECT upload_id FROM upload_sessions WHERE expires_at < ?",
            (now,),
        ).fetchall()
        for row in expired:
            chunk_dir = os.path.join(config.CHUNK_DIR, row[0])
            shutil.rmtree(chunk_dir, ignore_errors=True)
        conn.execute("DELETE FROM upload_sessions WHERE expires_at < ?", (now,))
        conn.commit()


def _active_reservation_bytes(user_id: str) -> int:
    with get_db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(total_bytes), 0) FROM upload_sessions "
            "WHERE user_id = ? AND expires_at >= ?",
            (user_id, time.time()),
        ).fetchone()
    return int(row[0]) if row else 0


def _create_upload_session(*, upload_id: str, user_id: str, dest_rel: str,
                            filename: str, total_bytes: int, total_chunks: int,
                            created_at: float, expires_at: float):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO upload_sessions (upload_id, user_id, dest_rel_path, filename, "
            "total_bytes, total_chunks, received_bytes, created_at, expires_at) "
            "VALUES (?,?,?,?,?,?,0,?,?)",
            (upload_id, user_id, dest_rel, filename, total_bytes, total_chunks, created_at, expires_at),
        )
        conn.commit()


def _get_upload_session(upload_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM upload_sessions WHERE upload_id = ?",
            (upload_id,),
        ).fetchone()
        return dict(row) if row else None


def _release_upload_session(upload_id: str):
    with get_db() as conn:
        conn.execute("DELETE FROM upload_sessions WHERE upload_id = ?", (upload_id,))
        conn.commit()


def _abort_upload_session(upload_id: str, chunk_dir: str, partial_path: str | None = None):
    if partial_path and os.path.exists(partial_path):
        try:
            os.remove(partial_path)
        except OSError:
            pass
    shutil.rmtree(chunk_dir, ignore_errors=True)
    _release_upload_session(upload_id)


def _received_bytes(upload_id: str) -> int:
    with get_db() as conn:
        row = conn.execute(
            "SELECT received_bytes FROM upload_sessions WHERE upload_id = ?",
            (upload_id,),
        ).fetchone()
    return int(row[0]) if row else 0


def _bump_received_bytes(upload_id: str, n: int):
    with get_db() as conn:
        conn.execute(
            "UPDATE upload_sessions SET received_bytes = received_bytes + ? "
            "WHERE upload_id = ?",
            (n, upload_id),
        )
        conn.commit()


@router.get("/files")
def list_files(request: Request, path: str = "/"):
    user = _user(request)
    abs_path, canonical_rel = resolve_and_authorize(user, path, "read")

    if not os.path.isdir(abs_path):
        raise HTTPException(status_code=404, detail="Directory not found")

    items = fs.list_directory(abs_path)

    # For regular users, remap paths to be relative to their home
    if user["role"] == "user":
        home_prefix = f"/{config.HOMES_DIR}/{user['username']}"
        for item in items:
            if item["path"].startswith(home_prefix):
                item["path"] = item["path"][len(home_prefix):] or "/"

    display_path = canonical_rel
    if user["role"] == "user":
        home_prefix = f"/{config.HOMES_DIR}/{user['username']}"
        if display_path.startswith(home_prefix):
            display_path = display_path[len(home_prefix):] or "/"

    parent = None
    if display_path != "/":
        parent = os.path.dirname(display_path)

    return {"path": display_path, "parent": parent, "items": items}


@router.get("/files/download")
def download_file(request: Request, path: str):
    user = _user(request)
    abs_path, _ = resolve_and_authorize(user, path, "read")
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="File not found")
    mime = fs.get_mime_type(os.path.basename(abs_path))
    return FileResponse(abs_path, media_type=mime, filename=os.path.basename(abs_path))


@router.get("/files/download-zip")
def download_zip(request: Request, path: str):
    user = _user(request)
    abs_path, _ = resolve_and_authorize(user, path, "read")
    if not os.path.isdir(abs_path):
        raise HTTPException(status_code=404, detail="Directory not found")
    folder_name = os.path.basename(abs_path) or "download"
    return StreamingResponse(
        stream_zip(abs_path, folder_name),
        media_type="application/zip",
        headers={"Content-Disposition": fs.content_disposition(f"{folder_name}.zip")},
    )


@router.get("/files/download-batch")
def download_batch(request: Request, paths: list[str] = Query(...)):
    """Stream a single zip built from multiple selected paths.

    Browsers throttle / block consecutive popup downloads, so the toolbar
    can't fire one window.open() per selection. This endpoint takes the
    full selection as repeated `paths=` query params and zips whatever the
    caller is allowed to read; unauthorized or missing paths are silently
    skipped (already non-visible to that user).
    """
    user = _user(request)
    if not paths:
        raise HTTPException(status_code=400, detail="No paths")

    entries: list[tuple[str, str]] = []
    seen_names: dict[str, int] = {}
    skipped_forbidden = 0
    skipped_missing = 0
    for p in paths:
        try:
            abs_path, _ = resolve_and_authorize(user, p, "read")
        except HTTPException:
            skipped_forbidden += 1
            continue
        if not os.path.exists(abs_path):
            skipped_missing += 1
            continue
        arc_root = os.path.basename(abs_path) or "file"
        # Disambiguate collisions when distinct paths share a basename.
        n = seen_names.get(arc_root, 0)
        seen_names[arc_root] = n + 1
        if n:
            stem, ext = os.path.splitext(arc_root)
            arc_root = f"{stem} ({n}){ext}"
        entries.append((abs_path, arc_root))

    if not entries:
        raise HTTPException(status_code=404, detail="Nothing to download")

    return StreamingResponse(
        stream_zip_entries(entries),
        media_type="application/zip",
        headers={
            "Content-Disposition": fs.content_disposition("nkcloud-download.zip"),
            # Counts let an admin diagnose "my zip is missing files" without
            # rummaging through audit logs; the streaming body has no room
            # for a structured failed[] like move/delete return.
            "X-Nkcloud-Total": str(len(paths)),
            "X-Nkcloud-Included": str(len(entries)),
            "X-Nkcloud-Skipped-Forbidden": str(skipped_forbidden),
            "X-Nkcloud-Skipped-Missing": str(skipped_missing),
        },
    )


@router.get("/files/stream")
def stream_file(request: Request, path: str):
    user = _user(request)
    abs_path, _ = resolve_and_authorize(user, path, "read")
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="File not found")
    mime = fs.get_mime_type(os.path.basename(abs_path))
    return FileResponse(abs_path, media_type=mime)


@router.post("/files/upload")
async def upload_files(
    request: Request,
    path: str = Form("/"),
    files: list[UploadFile] = File(...),
):
    user = _user(request)
    abs_dir, canonical_rel = resolve_and_authorize(user, path, "upload")
    if not os.path.isdir(abs_dir):
        raise HTTPException(status_code=404, detail="Directory not found")

    uploaded = []
    total_size = 0
    for f in files:
        if not f.filename:
            continue
        safe_name = os.path.basename(f.filename)
        dest = os.path.join(abs_dir, safe_name)
        if os.path.exists(dest):
            base, ext = os.path.splitext(safe_name)
            counter = 1
            while os.path.exists(dest):
                dest = os.path.join(abs_dir, f"{base} ({counter}){ext}")
                counter += 1
        with open(dest, "wb") as out:
            while chunk := await f.read(1024 * 1024):
                total_size += len(chunk)
                # Check quota mid-upload for early rejection
                if user["quota_bytes"] > 0 and user["used_bytes"] + total_size > user["quota_bytes"]:
                    out.close()
                    os.remove(dest)
                    raise HTTPException(status_code=413, detail="Storage quota exceeded")
                out.write(chunk)
        uploaded.append(os.path.basename(dest))

    record_audit(user["id"], user["username"], "upload", target_path=canonical_rel,
                 detail=json.dumps({"files": uploaded}),
                 ip=_client_ip(request))

    # Update used_bytes (delta, not full recompute)
    if user["role"] != "owner" and total_size:
        _adjust_used_bytes(user["id"], total_size)

    return {"uploaded": uploaded}


@router.post("/files/upload/init")
def init_chunked_upload(
    request: Request,
    path: str = Form("/"),
    filename: str = Form(...),
    total_bytes: int = Form(...),
    total_chunks: int = Form(...),
):
    """Open a server-tracked upload session with pre-reserved quota.

    This closes the unbounded /chunk DoS: previously any caller could POST
    arbitrary chunks to /chunks/<their-own-uuid> and fill the chunk directory
    without ever completing, bypassing per-user quotas entirely.
    """
    user = _user(request)
    abs_dir, canonical_rel = resolve_and_authorize(user, path, "upload")
    if not os.path.isdir(abs_dir):
        raise HTTPException(status_code=404, detail="Directory not found")

    if total_bytes < 0 or total_bytes > config.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large")
    if total_chunks < 1 or total_chunks > config.MAX_CHUNKS_PER_UPLOAD:
        raise HTTPException(status_code=400, detail="Invalid chunk count")
    safe_name = os.path.basename(filename)
    if not safe_name or "/" in safe_name or "\0" in safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Purge expired sessions before reservation check so they don't consume
    # phantom quota forever.
    _purge_expired_upload_sessions()

    if user["role"] != "owner":
        reserved = _active_reservation_bytes(user["id"])
        if user["quota_bytes"] > 0:
            available = user["quota_bytes"] - user["used_bytes"] - reserved
            if total_bytes > available:
                raise HTTPException(status_code=413, detail="Storage quota exceeded")

    upload_id = uuid.uuid4().hex
    now = time.time()
    _create_upload_session(
        upload_id=upload_id, user_id=user["id"], dest_rel=canonical_rel,
        filename=safe_name, total_bytes=total_bytes, total_chunks=total_chunks,
        created_at=now, expires_at=now + config.UPLOAD_SESSION_TTL_SECONDS,
    )
    os.makedirs(os.path.join(config.CHUNK_DIR, upload_id), exist_ok=True)
    return {"upload_id": upload_id, "expires_at": now + config.UPLOAD_SESSION_TTL_SECONDS}


@router.post("/files/upload/chunk")
async def upload_chunk(
    request: Request,
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),  # kept for compat with old clients; ignored
    filename: str = Form(...),  # kept for compat; session is source of truth
    file: UploadFile = File(...),
):
    user = _user(request)
    session = _get_upload_session(upload_id)
    if not session or session["user_id"] != user["id"]:
        raise HTTPException(status_code=400, detail="Upload session not found")
    if session["expires_at"] < time.time():
        raise HTTPException(status_code=410, detail="Upload session expired")
    if chunk_index < 0 or chunk_index >= session["total_chunks"]:
        raise HTTPException(status_code=400, detail="Invalid chunk index")

    chunk_dir = os.path.join(config.CHUNK_DIR, upload_id)
    os.makedirs(chunk_dir, exist_ok=True)
    chunk_path = os.path.join(chunk_dir, f"{chunk_index:06d}")

    # Per-chunk cap: a chunk can never be bigger than the whole file. This
    # doesn't rely on received_bytes (which races under concurrent workers);
    # /complete re-validates the assembled total.
    per_chunk_cap = session["total_bytes"] + 1024 * 1024
    written = 0
    with open(chunk_path, "wb") as out:
        while data := await file.read(1024 * 1024):
            written += len(data)
            if written > per_chunk_cap:
                out.close()
                try:
                    os.remove(chunk_path)
                except OSError:
                    pass
                raise HTTPException(status_code=413, detail="Chunk exceeds declared total")
            out.write(data)
    _bump_received_bytes(upload_id, written)
    return {"chunk_index": chunk_index, "received": True}


@router.post("/files/upload/complete")
def complete_chunked_upload(
    request: Request,
    upload_id: str = Form(...),
    total_chunks: int = Form(...),  # compat; session is source of truth
    filename: str = Form(...),  # compat
    path: str = Form("/"),  # compat
):
    user = _user(request)
    session = _get_upload_session(upload_id)
    if not session or session["user_id"] != user["id"]:
        raise HTTPException(status_code=400, detail="Upload session not found")

    # Re-authorize the destination every time — role/permissions may have
    # changed between /init and /complete.
    abs_dir, canonical_rel = resolve_and_authorize(user, session["dest_rel_path"], "upload")
    if not os.path.isdir(abs_dir):
        _release_upload_session(upload_id)
        raise HTTPException(status_code=404, detail="Directory not found")

    chunk_dir = os.path.join(config.CHUNK_DIR, upload_id)
    if not os.path.isdir(chunk_dir):
        _release_upload_session(upload_id)
        raise HTTPException(status_code=400, detail="Upload not found")

    total_size = 0
    missing_chunks = []
    for i in range(session["total_chunks"]):
        chunk_path = os.path.join(chunk_dir, f"{i:06d}")
        if os.path.exists(chunk_path):
            total_size += os.path.getsize(chunk_path)
        else:
            missing_chunks.append(i)

    if missing_chunks:
        _abort_upload_session(upload_id, chunk_dir)
        raise HTTPException(status_code=400, detail=f"Missing chunk {missing_chunks[0]}")

    # Final ceiling check: assembled file cannot exceed the declared size
    # (plus 1 MB slack for multipart overhead). Defends against a client
    # spreading a lying upload across many chunks that each individually pass
    # the per-chunk cap.
    if total_size > session["total_bytes"] + 1024 * 1024:
        _abort_upload_session(upload_id, chunk_dir)
        raise HTTPException(status_code=413, detail="Assembled size exceeds declared total")

    safe_name = session["filename"]
    dest = os.path.join(abs_dir, safe_name)
    if os.path.exists(dest):
        base, ext = os.path.splitext(safe_name)
        counter = 1
        while os.path.exists(dest):
            dest = os.path.join(abs_dir, f"{base} ({counter}){ext}")
            counter += 1

    try:
        with open(dest, "wb") as out:
            for i in range(session["total_chunks"]):
                chunk_path = os.path.join(chunk_dir, f"{i:06d}")
                with open(chunk_path, "rb") as cf:
                    while data := cf.read(1024 * 1024):
                        out.write(data)
    except OSError:
        _abort_upload_session(upload_id, chunk_dir, dest)
        raise HTTPException(status_code=500, detail="Could not assemble upload")

    _abort_upload_session(upload_id, chunk_dir)

    record_audit(user["id"], user["username"], "upload", target_path=canonical_rel,
                 detail=json.dumps({"file": os.path.basename(dest), "size": total_size}),
                 ip=_client_ip(request))

    if user["role"] != "owner" and total_size:
        _adjust_used_bytes(user["id"], total_size)

    return {"filename": os.path.basename(dest), "path": fs.relative_path(dest)}


@router.post("/files/mkdir")
def make_directory(payload: MkdirPayload, request: Request):
    user = _user(request)
    abs_path, canonical_rel = resolve_parent_and_authorize(user, payload.path, "mkdir")
    if os.path.exists(abs_path):
        raise HTTPException(status_code=409, detail="Already exists")
    os.makedirs(abs_path)

    record_audit(user["id"], user["username"], "mkdir", target_path=canonical_rel,
                 ip=_client_ip(request))

    return {"path": fs.relative_path(abs_path)}


@router.post("/files/rename")
def rename_file(payload: RenamePayload, request: Request):
    user = _user(request)
    abs_path, canonical_rel = resolve_and_authorize(user, payload.path, "rename")
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="Not found")
    safe_name = os.path.basename(payload.new_name)
    if not safe_name or safe_name.startswith(".") or "/" in safe_name or "\0" in safe_name:
        raise HTTPException(status_code=400, detail="Invalid name")
    new_path = os.path.join(os.path.dirname(abs_path), safe_name)
    if os.path.exists(new_path):
        raise HTTPException(status_code=409, detail="Name already exists")
    os.rename(abs_path, new_path)

    record_audit(user["id"], user["username"], "rename", target_path=canonical_rel,
                 detail=json.dumps({"new_name": safe_name}),
                 ip=_client_ip(request))

    return {"path": fs.relative_path(new_path)}


@router.post("/files/move")
def move_files(payload: MovePayload, request: Request):
    user = _user(request)
    dest_dir, dest_rel = resolve_and_authorize(user, payload.destination, "write")
    if not os.path.isdir(dest_dir):
        raise HTTPException(status_code=404, detail="Destination not found")

    moved = []
    failed: list[dict] = []
    for p in payload.paths:
        try:
            src, _src_rel = resolve_and_authorize(user, p, "read")
            # Need delete permission on source too.
            _src_abs, src_canonical = resolve_and_authorize(user, p, "delete")
        except HTTPException:
            failed.append({"path": p, "reason": "forbidden"})
            continue
        if not os.path.exists(src):
            failed.append({"path": p, "reason": "not_found"})
            continue
        target = os.path.join(dest_dir, os.path.basename(src))
        if os.path.exists(target):
            base, ext = os.path.splitext(os.path.basename(src))
            counter = 1
            while os.path.exists(target):
                target = os.path.join(dest_dir, f"{base} ({counter}){ext}")
                counter += 1
        try:
            shutil.move(src, target)
        except (OSError, shutil.Error):
            failed.append({"path": p, "reason": "io_error"})
            continue
        moved.append(os.path.basename(target))

    if moved:
        record_audit(user["id"], user["username"], "move",
                     target_path=dest_rel,
                     detail=json.dumps({"files": moved}),
                     ip=_client_ip(request))

    return {"moved": moved, "failed": failed}


@router.delete("/files")
def delete_files(payload: DeletePayload, request: Request):
    """Soft-delete: move items to the user's trash."""
    user = _user(request)
    trashed = []
    failed: list[dict] = []
    file_root = os.path.realpath(config.FILE_ROOT)
    user_home = os.path.join(file_root, config.HOMES_DIR, user["username"])
    for p in payload.paths:
        try:
            abs_path, canonical_rel = resolve_and_authorize(user, p, "delete")
        except HTTPException:
            failed.append({"path": p, "reason": "forbidden"})
            continue
        if not os.path.exists(abs_path):
            failed.append({"path": p, "reason": "not_found"})
            continue
        # Refuse to trash the file root or a user's home root —
        # would attempt to move a directory into itself.
        if abs_path == file_root or abs_path == user_home:
            failed.append({"path": p, "reason": "root_protected"})
            continue
        try:
            trash_svc.move_to_trash(abs_path, canonical_rel, user)
            trashed.append(p)
        except (OSError, shutil.Error):
            failed.append({"path": p, "reason": "io_error"})
            continue

    if trashed:
        record_audit(user["id"], user["username"], "delete",
                     detail=json.dumps({"paths": trashed, "soft": True}),
                     ip=_client_ip(request))
        # used_bytes unchanged — trash still counts against the user's home.

    return {"deleted": trashed, "failed": failed, "soft": True}


# --- Trash ---

@router.get("/files/trash")
def list_trash(request: Request):
    user = _user(request)
    # Lazy purge expired entries on every list
    purged = trash_svc.purge_expired(user)
    if user["role"] != "owner" and purged["bytes_freed"]:
        _adjust_used_bytes(user["id"], -purged["bytes_freed"])

    display_prefix = None
    if user["role"] == "user":
        display_prefix = f"/{config.HOMES_DIR}/{user['username']}"

    entries = trash_svc.list_trash(user, display_prefix=display_prefix)
    return {
        "entries": entries,
        "retention_days": config.TRASH_RETENTION_DAYS,
    }


@router.post("/files/trash/restore")
def restore_from_trash(payload: TrashPayload, request: Request):
    user = _user(request)
    result = trash_svc.restore_ids(user, payload.ids)
    if result["restored"]:
        record_audit(user["id"], user["username"], "restore",
                     detail=json.dumps({"ids": result["restored"]}),
                     ip=_client_ip(request))
        # Restored item stays inside the user's home tree — used_bytes unchanged.
    return result


@router.delete("/files/trash")
def purge_trash_items(payload: TrashPayload, request: Request):
    user = _user(request)
    result = trash_svc.purge_ids(user, payload.ids)
    removed = result["removed"]
    if removed:
        record_audit(user["id"], user["username"], "purge",
                     detail=json.dumps({"ids": removed}),
                     ip=_client_ip(request))
        if user["role"] != "owner" and result["bytes_freed"]:
            _adjust_used_bytes(user["id"], -result["bytes_freed"])
    return {"purged": removed}


@router.post("/files/trash/empty")
def empty_trash(request: Request):
    user = _user(request)
    result = trash_svc.empty_trash(user)
    count = result["count"]
    if count:
        record_audit(user["id"], user["username"], "purge_all",
                     detail=json.dumps({"count": count}),
                     ip=_client_ip(request))
        if user["role"] != "owner" and result["bytes_freed"]:
            _adjust_used_bytes(user["id"], -result["bytes_freed"])
    return {"purged_count": count}


# --- Text preview ---

@router.get("/files/text")
def preview_text(request: Request, path: str):
    """Return plain-text content for small text files. UTF-8 decoded with fallback."""
    user = _user(request)
    abs_path, _ = resolve_and_authorize(user, path, "read")
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="File not found")

    ext = os.path.splitext(abs_path)[1].lower()
    size = os.path.getsize(abs_path)
    is_whitelisted = ext in config.TEXT_PREVIEW_EXTS

    if size > config.TEXT_PREVIEW_MAX_BYTES:
        raise HTTPException(status_code=413, detail="File too large for preview")

    with open(abs_path, "rb") as f:
        raw = f.read()

    # Try strict UTF-8 first; if fails and not whitelisted, refuse (likely binary)
    try:
        text = raw.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        if not is_whitelisted:
            raise HTTPException(status_code=415, detail="Not a text file")
        # Whitelisted non-UTF8: try common fallbacks
        for enc in ("utf-8-sig", "gbk", "big5", "latin-1"):
            try:
                text = raw.decode(enc)
                encoding = enc
                break
            except UnicodeDecodeError:
                continue
        else:
            raise HTTPException(status_code=415, detail="Could not decode file")

    return {
        "name": os.path.basename(abs_path),
        "ext": ext.lstrip("."),
        "size": size,
        "encoding": encoding,
        "content": text,
    }


@router.get("/stats")
def disk_stats(request: Request):
    user = _user(request)
    if user["role"] == "owner":
        usage = shutil.disk_usage(config.FILE_ROOT)
        return {"total": usage.total, "used": usage.used, "free": usage.free}
    else:
        # Return user's quota info
        return {
            "total": user["quota_bytes"] if user["quota_bytes"] > 0 else None,
            "used": user["used_bytes"],
            "free": (user["quota_bytes"] - user["used_bytes"]) if user["quota_bytes"] > 0 else None,
        }
