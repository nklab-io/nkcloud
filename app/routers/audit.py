from fastapi import APIRouter, HTTPException, Request

from ..database import get_db

router = APIRouter(tags=["audit"])


def _require_owner(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if user["role"] != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    return user


@router.get("/api/audit")
def get_audit_log(
    request: Request,
    page: int = 1,
    limit: int = 50,
    user_id: str | None = None,
    action: str | None = None,
):
    _require_owner(request)

    if limit > 200:
        limit = 200
    offset = (page - 1) * limit

    conditions = []
    params = []
    if user_id:
        conditions.append("user_id = ?")
        params.append(user_id)
    if action:
        conditions.append("action = ?")
        params.append(action)

    where = " AND ".join(conditions)
    if where:
        where = "WHERE " + where

    with get_db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM audit_log {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM audit_log {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "entries": [dict(r) for r in rows],
    }
