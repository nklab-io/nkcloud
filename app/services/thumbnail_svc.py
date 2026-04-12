import hashlib
import os
import subprocess

from PIL import Image

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

from .. import config
from .filesystem import THUMBABLE_IMAGE, THUMBABLE_VIDEO


def _cache_key(file_path: str, mtime: float, size_tag: str) -> str:
    raw = f"{file_path}:{mtime}:{size_tag}".encode()
    return hashlib.sha256(raw).hexdigest()


def get_thumbnail_path(file_path: str, size: str = "thumb") -> str | None:
    """Return cached thumbnail path, generating if needed.

    size: "thumb" (small grid preview) or "preview" (larger render for lightbox).
    """
    if not os.path.isfile(file_path):
        return None

    dims = config.PREVIEW_SIZE if size == "preview" else config.THUMB_SIZE
    ext = os.path.splitext(file_path)[1].lower()
    st = os.stat(file_path)
    key = _cache_key(file_path, st.st_mtime, size)
    thumb_path = os.path.join(config.THUMB_DIR, f"{key}.webp")

    if os.path.exists(thumb_path):
        return thumb_path

    try:
        if ext in THUMBABLE_IMAGE:
            return _generate_image_thumb(file_path, thumb_path, dims, size)
        elif ext in THUMBABLE_VIDEO and size == "thumb":
            return _generate_video_thumb(file_path, thumb_path, dims)
    except Exception:
        return None
    return None


def _generate_image_thumb(src: str, dst: str, dims: tuple[int, int], size: str) -> str | None:
    ext = os.path.splitext(src)[1].lower()
    if ext == ".svg":
        return None
    try:
        with Image.open(src) as img:
            img.thumbnail(dims)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            quality = 85 if size == "preview" else 80
            img.save(dst, "WEBP", quality=quality)
        return dst
    except Exception:
        return None


def _generate_video_thumb(src: str, dst: str, dims: tuple[int, int]) -> str | None:
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-i", src,
                "-ss", "00:00:03",
                "-vframes", "1",
                "-vf", f"scale={dims[0]}:-1",
                "-f", "image2",
                "-c:v", "webp",
                "-y", dst,
            ],
            capture_output=True,
            timeout=15,
        )
        if result.returncode == 0 and os.path.exists(dst):
            return dst
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None
