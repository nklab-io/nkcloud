from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from ..permissions import resolve_and_authorize
from ..services.thumbnail_svc import get_thumbnail_path

router = APIRouter(tags=["thumbnails"])


@router.get("/thumb")
def get_thumbnail(request: Request, path: str, size: str = "thumb"):
    """Render or fetch a cached thumbnail.

    This endpoint used to run only safe_resolve, so any authenticated user
    could pull thumbs of any file anywhere in FILE_ROOT (including other
    users' homes and .trash). resolve_and_authorize applies the same scope
    + trash rules as /files/download.
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    abs_path, _ = resolve_and_authorize(user, path, "read")
    if size not in ("thumb", "preview"):
        size = "thumb"
    thumb = get_thumbnail_path(abs_path, size=size)
    if not thumb:
        raise HTTPException(status_code=404, detail="No thumbnail available")
    return FileResponse(thumb, media_type="image/webp")
