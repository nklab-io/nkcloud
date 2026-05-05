import os

from fastapi import APIRouter, HTTPException, Request

from ..services import filesystem as fs
from ..permissions import resolve_and_authorize
from .. import config

router = APIRouter(tags=["search"])


@router.get("/search")
def search_files(request: Request, q: str, path: str = "/"):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not q or len(q) < 2:
        raise HTTPException(status_code=400, detail="Query too short")

    abs_path, _ = resolve_and_authorize(user, path, "read")
    if not os.path.isdir(abs_path):
        raise HTTPException(status_code=404, detail="Directory not found")

    query_lower = q.lower()
    results = []
    home_prefix = f"/{config.HOMES_DIR}/{user['username']}" if user["role"] == "user" else None

    search_root = os.path.realpath(abs_path)
    for root, dirs, files in os.walk(search_root, followlinks=False):
        dirs[:] = [
            d for d in dirs
            if not d.startswith(".")
            and fs.is_within_root(os.path.join(root, d), search_root)
        ]
        for name in dirs + files:
            if query_lower in name.lower():
                full = os.path.join(root, name)
                real_full = os.path.realpath(full)
                if not fs.is_within_root(real_full, search_root):
                    continue
                is_dir = os.path.isdir(full)
                try:
                    st = os.stat(real_full)
                except OSError:
                    continue
                rel_path = fs.relative_path(full)
                if home_prefix and rel_path.startswith(home_prefix):
                    rel_path = rel_path[len(home_prefix):] or "/"
                results.append({
                    "name": name,
                    "path": rel_path,
                    "is_dir": is_dir,
                    "size": 0 if is_dir else st.st_size,
                    "mime_type": "" if is_dir else fs.get_mime_type(name),
                })
                if len(results) >= config.MAX_SEARCH_RESULTS:
                    return {"results": results, "truncated": True}

    return {"results": results, "truncated": False}
