import json
import os
import shutil
import uuid

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse

from ..models import MkdirPayload, RenamePayload, MovePayload, DeletePayload
from ..services import filesystem as fs
from ..services.zip_svc import stream_zip
from ..permissions import check_permission, remap_path_for_user
from ..database import record_audit, get_db
from .. import config

router = APIRouter(tags=["files"])


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


def _check_quota(user: dict, additional_bytes: int):
    """Raise 413 if user would exceed quota."""
    if user["quota_bytes"] > 0:
        if user["used_bytes"] + additional_bytes > user["quota_bytes"]:
            raise HTTPException(status_code=413, detail="Storage quota exceeded")


def _update_used_bytes(user: dict):
    """Recalculate and update used_bytes for a user."""
    home_dir = os.path.join(config.FILE_ROOT, config.HOMES_DIR, user["username"])
    if os.path.isdir(home_dir):
        size = fs.get_directory_size(home_dir)
    else:
        size = 0
    with get_db() as conn:
        conn.execute("UPDATE users SET used_bytes = ? WHERE id = ?", (size, user["id"]))
        conn.commit()


@router.get("/files")
def list_files(request: Request, path: str = "/"):
    user = _user(request)
    path = remap_path_for_user(path, user)
    rel = path  # store before resolve for permission check
    check_permission(user, "read", rel)

    try:
        abs_path = fs.safe_resolve(path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not os.path.isdir(abs_path):
        raise HTTPException(status_code=404, detail="Directory not found")

    items = fs.list_directory(abs_path)

    # For regular users, remap paths to be relative to their home
    if user["role"] == "user":
        home_prefix = f"/{config.HOMES_DIR}/{user['username']}"
        for item in items:
            if item["path"].startswith(home_prefix):
                item["path"] = item["path"][len(home_prefix):] or "/"

    actual_rel = fs.relative_path(abs_path)
    display_path = actual_rel
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
    path = remap_path_for_user(path, user)
    check_permission(user, "read", path)

    try:
        abs_path = fs.safe_resolve(path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="File not found")
    mime = fs.get_mime_type(os.path.basename(abs_path))
    return FileResponse(abs_path, media_type=mime, filename=os.path.basename(abs_path))


@router.get("/files/download-zip")
def download_zip(request: Request, path: str):
    user = _user(request)
    path = remap_path_for_user(path, user)
    check_permission(user, "read", path)

    try:
        abs_path = fs.safe_resolve(path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not os.path.isdir(abs_path):
        raise HTTPException(status_code=404, detail="Directory not found")
    folder_name = os.path.basename(abs_path) or "download"
    return StreamingResponse(
        stream_zip(abs_path, folder_name),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{folder_name}.zip"'},
    )


@router.get("/files/stream")
def stream_file(request: Request, path: str):
    user = _user(request)
    path = remap_path_for_user(path, user)
    check_permission(user, "read", path)

    try:
        abs_path = fs.safe_resolve(path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
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
    path = remap_path_for_user(path, user)
    check_permission(user, "upload", path)

    try:
        abs_dir = fs.safe_resolve(path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
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

    record_audit(user["id"], user["username"], "upload", target_path=path,
                 detail=json.dumps({"files": uploaded}),
                 ip=_client_ip(request))

    # Update used_bytes
    if user["role"] != "owner":
        _update_used_bytes(user)

    return {"uploaded": uploaded}


@router.post("/files/upload/chunk")
async def upload_chunk(
    request: Request,
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    filename: str = Form(...),
    file: UploadFile = File(...),
):
    _user(request)  # auth check only
    chunk_dir = os.path.join(config.CHUNK_DIR, upload_id)
    os.makedirs(chunk_dir, exist_ok=True)
    chunk_path = os.path.join(chunk_dir, f"{chunk_index:06d}")
    with open(chunk_path, "wb") as out:
        while data := await file.read(1024 * 1024):
            out.write(data)
    return {"chunk_index": chunk_index, "received": True}


@router.post("/files/upload/complete")
def complete_chunked_upload(
    request: Request,
    upload_id: str = Form(...),
    total_chunks: int = Form(...),
    filename: str = Form(...),
    path: str = Form("/"),
):
    user = _user(request)
    path = remap_path_for_user(path, user)
    check_permission(user, "upload", path)

    try:
        abs_dir = fs.safe_resolve(path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not os.path.isdir(abs_dir):
        raise HTTPException(status_code=404, detail="Directory not found")

    chunk_dir = os.path.join(config.CHUNK_DIR, upload_id)
    if not os.path.isdir(chunk_dir):
        raise HTTPException(status_code=400, detail="Upload not found")

    # Calculate total size for quota check
    total_size = 0
    for i in range(total_chunks):
        chunk_path = os.path.join(chunk_dir, f"{i:06d}")
        if os.path.exists(chunk_path):
            total_size += os.path.getsize(chunk_path)
    _check_quota(user, total_size)

    safe_name = os.path.basename(filename)
    dest = os.path.join(abs_dir, safe_name)
    if os.path.exists(dest):
        base, ext = os.path.splitext(safe_name)
        counter = 1
        while os.path.exists(dest):
            dest = os.path.join(abs_dir, f"{base} ({counter}){ext}")
            counter += 1

    with open(dest, "wb") as out:
        for i in range(total_chunks):
            chunk_path = os.path.join(chunk_dir, f"{i:06d}")
            if not os.path.exists(chunk_path):
                raise HTTPException(status_code=400, detail=f"Missing chunk {i}")
            with open(chunk_path, "rb") as cf:
                while data := cf.read(1024 * 1024):
                    out.write(data)

    shutil.rmtree(chunk_dir, ignore_errors=True)

    record_audit(user["id"], user["username"], "upload", target_path=path,
                 detail=json.dumps({"file": os.path.basename(dest), "size": total_size}),
                 ip=_client_ip(request))

    if user["role"] != "owner":
        _update_used_bytes(user)

    return {"filename": os.path.basename(dest), "path": fs.relative_path(dest)}


@router.post("/files/mkdir")
def make_directory(payload: MkdirPayload, request: Request):
    user = _user(request)
    path = remap_path_for_user(payload.path, user)
    check_permission(user, "mkdir", path)

    try:
        abs_path = fs.safe_resolve(path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
    if os.path.exists(abs_path):
        raise HTTPException(status_code=409, detail="Already exists")
    os.makedirs(abs_path)

    record_audit(user["id"], user["username"], "mkdir", target_path=path,
                 ip=_client_ip(request))

    return {"path": fs.relative_path(abs_path)}


@router.post("/files/rename")
def rename_file(payload: RenamePayload, request: Request):
    user = _user(request)
    path = remap_path_for_user(payload.path, user)
    check_permission(user, "rename", path)

    try:
        abs_path = fs.safe_resolve(path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="Not found")
    safe_name = os.path.basename(payload.new_name)
    if not safe_name or safe_name.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid name")
    new_path = os.path.join(os.path.dirname(abs_path), safe_name)
    if os.path.exists(new_path):
        raise HTTPException(status_code=409, detail="Name already exists")
    os.rename(abs_path, new_path)

    record_audit(user["id"], user["username"], "rename", target_path=path,
                 detail=json.dumps({"new_name": safe_name}),
                 ip=_client_ip(request))

    return {"path": fs.relative_path(new_path)}


@router.post("/files/move")
def move_files(payload: MovePayload, request: Request):
    user = _user(request)
    dest_path = remap_path_for_user(payload.destination, user)
    check_permission(user, "write", dest_path)

    try:
        dest_dir = fs.safe_resolve(dest_path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid destination")
    if not os.path.isdir(dest_dir):
        raise HTTPException(status_code=404, detail="Destination not found")

    moved = []
    for p in payload.paths:
        src_path = remap_path_for_user(p, user)
        check_permission(user, "read", src_path)
        check_permission(user, "delete", src_path)
        try:
            src = fs.safe_resolve(src_path)
        except ValueError:
            continue
        if not os.path.exists(src):
            continue
        target = os.path.join(dest_dir, os.path.basename(src))
        if os.path.exists(target):
            base, ext = os.path.splitext(os.path.basename(src))
            counter = 1
            while os.path.exists(target):
                target = os.path.join(dest_dir, f"{base} ({counter}){ext}")
                counter += 1
        shutil.move(src, target)
        moved.append(os.path.basename(target))

    if moved:
        record_audit(user["id"], user["username"], "move",
                     target_path=dest_path,
                     detail=json.dumps({"files": moved}),
                     ip=_client_ip(request))
        if user["role"] != "owner":
            _update_used_bytes(user)

    return {"moved": moved}


@router.delete("/files")
def delete_files(payload: DeletePayload, request: Request):
    user = _user(request)
    deleted = []
    for p in payload.paths:
        path = remap_path_for_user(p, user)
        check_permission(user, "delete", path)
        try:
            abs_path = fs.safe_resolve(path)
        except ValueError:
            continue
        if not os.path.exists(abs_path):
            continue
        if os.path.isdir(abs_path):
            shutil.rmtree(abs_path)
        else:
            os.remove(abs_path)
        deleted.append(p)

    if deleted:
        record_audit(user["id"], user["username"], "delete",
                     detail=json.dumps({"paths": deleted}),
                     ip=_client_ip(request))
        if user["role"] != "owner":
            _update_used_bytes(user)

    return {"deleted": deleted}


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
