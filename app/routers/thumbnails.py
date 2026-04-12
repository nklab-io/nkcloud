from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..services import filesystem as fs
from ..services.thumbnail_svc import get_thumbnail_path

router = APIRouter(tags=["thumbnails"])


@router.get("/thumb")
def get_thumbnail(path: str, size: str = "thumb"):
    try:
        abs_path = fs.safe_resolve(path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
    if size not in ("thumb", "preview"):
        size = "thumb"
    thumb = get_thumbnail_path(abs_path, size=size)
    if not thumb:
        raise HTTPException(status_code=404, detail="No thumbnail available")
    return FileResponse(thumb, media_type="image/webp")
