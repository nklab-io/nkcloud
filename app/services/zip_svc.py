import os
import zipfile
from io import BytesIO
from typing import Generator


def stream_zip(dir_path: str, base_name: str) -> Generator[bytes, None, None]:
    """Stream a directory as a zip file without buffering the whole thing."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as zf:
        for root, dirs, files in os.walk(dir_path):
            # Skip hidden dirs
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in files:
                if fname.startswith("."):
                    continue
                full_path = os.path.join(root, fname)
                arc_name = os.path.join(base_name, os.path.relpath(full_path, dir_path))
                try:
                    zf.write(full_path, arc_name)
                except (PermissionError, OSError):
                    continue
                # Flush periodically
                if buffer.tell() > 1024 * 1024:
                    yield buffer.getvalue()
                    buffer.seek(0)
                    buffer.truncate()
    # Final flush
    remaining = buffer.getvalue()
    if remaining:
        yield remaining
