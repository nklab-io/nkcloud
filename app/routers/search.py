import os

from fastapi import APIRouter, HTTPException, Request

from ..services import filesystem as fs
from ..permissions import remap_path_for_user, check_permission
from .. import config

router = APIRouter(tags=["search"])


@router.get("/search")
def search_files(request: Request, q: str, path: str = "/"):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not q or len(q) < 2:
        raise HTTPException(status_code=400, detail="Query too short")

    path = remap_path_for_user(path, user)
    check_permission(user, "read", path)

    try:
        abs_path = fs.safe_resolve(path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not os.path.isdir(abs_path):
        raise HTTPException(status_code=404, detail="Directory not found")

    query_lower = q.lower()
    results = []
    home_prefix = f"/{config.HOMES_DIR}/{user['username']}" if user["role"] == "user" else None

    for root, dirs, files in os.walk(abs_path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in dirs + files:
            if query_lower in name.lower():
                full = os.path.join(root, name)
                is_dir = os.path.isdir(full)
                try:
                    st = os.stat(full)
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
