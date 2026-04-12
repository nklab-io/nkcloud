import os
import stat
from datetime import datetime, timezone

from .. import config

THUMBABLE_IMAGE = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".ico",
    ".heic", ".heif", ".avif", ".tiff", ".tif",
}
# Formats the browser cannot render natively — preview endpoint serves a
# rendered WebP instead of the raw file.
NEEDS_RENDERED_PREVIEW = {".heic", ".heif", ".tiff", ".tif"}

THUMBABLE_VIDEO = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".wmv", ".m4v"}
# Only formats HTML5 <video> can play natively. Others get thumbnail + download.
PREVIEWABLE_VIDEO = {".mp4", ".webm", ".mov", ".m4v"}
PREVIEWABLE_AUDIO = {".mp3", ".flac", ".ogg", ".wav", ".m4a", ".aac", ".wma", ".opus"}


def safe_resolve(relative_path: str) -> str:
    """Resolve a user-provided path against FILE_ROOT, preventing traversal."""
    if not relative_path:
        relative_path = "/"
    cleaned = os.path.normpath(relative_path.lstrip("/"))
    if cleaned == ".":
        cleaned = ""
    full = os.path.realpath(os.path.join(config.FILE_ROOT, cleaned))
    root = os.path.realpath(config.FILE_ROOT)
    if full != root and not full.startswith(root + os.sep):
        raise ValueError("Path traversal detected")
    return full


def relative_path(absolute_path: str) -> str:
    """Convert absolute path back to relative path from FILE_ROOT."""
    root = os.path.realpath(config.FILE_ROOT)
    if absolute_path == root:
        return "/"
    return "/" + os.path.relpath(absolute_path, root)


def get_mime_type(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    mime_map = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        ".svg": "image/svg+xml", ".ico": "image/x-icon",
        ".heic": "image/heic", ".heif": "image/heif", ".avif": "image/avif",
        ".tiff": "image/tiff", ".tif": "image/tiff",
        ".mp4": "video/mp4", ".mkv": "video/x-matroska", ".webm": "video/webm",
        ".avi": "video/x-msvideo", ".mov": "video/quicktime", ".m4v": "video/mp4",
        ".mp3": "audio/mpeg", ".flac": "audio/flac", ".ogg": "audio/ogg",
        ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac",
        ".opus": "audio/opus", ".wma": "audio/x-ms-wma",
        ".pdf": "application/pdf", ".zip": "application/zip",
        ".txt": "text/plain", ".md": "text/markdown",
        ".json": "application/json", ".xml": "application/xml",
        ".html": "text/html", ".css": "text/css", ".js": "application/javascript",
    }
    return mime_map.get(ext, "application/octet-stream")


def has_thumbnail(name: str) -> bool:
    ext = os.path.splitext(name)[1].lower()
    return ext in THUMBABLE_IMAGE or ext in THUMBABLE_VIDEO


def list_directory(dir_path: str) -> list[dict]:
    """List contents of a directory, returning file metadata."""
    entries = []
    try:
        with os.scandir(dir_path) as it:
            for entry in it:
                if entry.name.startswith("."):
                    continue
                try:
                    st = entry.stat(follow_symlinks=True)
                except OSError:
                    continue
                is_dir = stat.S_ISDIR(st.st_mode)
                mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
                entries.append({
                    "name": entry.name,
                    "path": relative_path(entry.path),
                    "is_dir": is_dir,
                    "size": 0 if is_dir else st.st_size,
                    "modified": mtime,
                    "mime_type": "" if is_dir else get_mime_type(entry.name),
                    "has_thumb": False if is_dir else has_thumbnail(entry.name),
                })
    except PermissionError:
        pass
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return entries


def get_directory_size(path: str) -> int:
    """Calculate total size of all files in a directory recursively."""
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total
