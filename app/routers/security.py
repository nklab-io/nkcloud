from fastapi import APIRouter, HTTPException, Request

from ..database import get_db

router = APIRouter(tags=["security"])


def _require_owner(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if user["role"] != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    return user


@router.get("/api/security/login-attempts")
def list_login_attempts(
    request: Request,
    limit: int = 200,
    since_id: int = 0,
    only_failed: bool = False,
):
    _require_owner(request)
    if limit > 500:
        limit = 500

    conditions = []
    params: list = []
    if since_id > 0:
        conditions.append("id > ?")
        params.append(since_id)
    if only_failed:
        conditions.append("success = 0")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    with get_db() as conn:
        rows = conn.execute(
            f"SELECT id, ip, attempted_at, success, username, user_agent "
            f"FROM login_attempts {where} ORDER BY id DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM login_attempts").fetchone()[0]
        fails_24h = conn.execute(
            "SELECT COUNT(*) FROM login_attempts WHERE success = 0 AND attempted_at > strftime('%s','now') - 86400"
        ).fetchone()[0]

    return {
        "total": total,
        "fails_24h": fails_24h,
        "entries": [dict(r) for r in rows],
    }
