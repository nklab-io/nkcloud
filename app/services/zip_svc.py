import os
import zipfile
from io import BytesIO
from typing import Generator, Iterable, Tuple


class _StreamBuffer:
    """File-like sink that lets ZipFile write while we drain.

    zipfile records each entry's local-header offset via tell(); resetting the
    underlying position by truncating mid-archive corrupts the central
    directory. Here tell() reports cumulative bytes and never goes backwards.
    Reporting seekable() == False also pushes ZipFile onto its streaming path
    (data descriptors instead of seek-back), so we never need to revisit
    earlier bytes.
    """

    def __init__(self):
        self._buf = BytesIO()
        self._pos = 0

    def write(self, data) -> int:
        n = self._buf.write(data)
        self._pos += n
        return n

    def tell(self) -> int:
        return self._pos

    def flush(self):
        pass

    def seekable(self) -> bool:
        return False

    def drain(self) -> bytes:
        chunk = self._buf.getvalue()
        if chunk:
            self._buf.seek(0)
            self._buf.truncate()
        return chunk


def _walk_entries(abs_path: str, arc_root: str) -> Iterable[Tuple[str, str]]:
    """Yield (abs_path, arc_path) pairs for a file or directory tree."""
    if os.path.isfile(abs_path):
        yield abs_path, arc_root
        return
    if not os.path.isdir(abs_path):
        return
    for root, dirs, files in os.walk(abs_path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fname in files:
            if fname.startswith("."):
                continue
            full = os.path.join(root, fname)
            arc = os.path.join(arc_root, os.path.relpath(full, abs_path))
            yield full, arc


def stream_zip_entries(entries: Iterable[Tuple[str, str]]) -> Generator[bytes, None, None]:
    """Stream a zip archive built from (abs_path, arc_root) pairs.

    Each entry may be a single file or a directory tree; directories land
    under arc_root/. ZIP_STORED keeps CPU low — media downloads are bandwidth
    bound anyway.
    """
    buf = _StreamBuffer()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
        for abs_path, arc_root in entries:
            for full, arc in _walk_entries(abs_path, arc_root):
                try:
                    zf.write(full, arc)
                except (PermissionError, OSError):
                    continue
                chunk = buf.drain()
                if chunk:
                    yield chunk
    chunk = buf.drain()
    if chunk:
        yield chunk


def stream_zip(dir_path: str, base_name: str) -> Generator[bytes, None, None]:
    """Stream a single directory as a zip archive."""
    yield from stream_zip_entries([(dir_path, base_name)])
