from fastapi import HTTPException

from . import config
from .database import get_owner_user


# Actions handled by check_permission. Kept here so resolve_and_authorize
# can validate callers aren't passing garbage strings.
_VALID_ACTIONS = {"read", "write", "upload", "mkdir", "rename", "delete"}


def get_path_owner(relative_path: str) -> str | None:
    """Determine who owns a path based on its location.

    Returns username if path is under /_homes/{username}/,
    Returns None if path is in root area (owned by owner).
    """
    parts = relative_path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == config.HOMES_DIR:
        return parts[1]
    return None


def _is_inside_homes(relative_path: str) -> bool:
    """Check if path is inside the _homes directory."""
    return relative_path.strip("/").startswith(config.HOMES_DIR + "/") or relative_path.strip("/") == config.HOMES_DIR


def can_read(user: dict, relative_path: str) -> bool:
    role = user["role"]
    if role == "owner":
        return True
    if role == "admin":
        return True
    # Regular user: only own home
    path_owner = get_path_owner(relative_path)
    return path_owner == user["username"]


def can_write(user: dict, relative_path: str) -> bool:
    role = user["role"]
    if role == "owner":
        return True
    if role == "admin":
        # Admin can write inside _homes/ but NOT in root
        return _is_inside_homes(relative_path)
    # Regular user: only own home
    path_owner = get_path_owner(relative_path)
    return path_owner == user["username"]


def can_delete(user: dict, relative_path: str) -> bool:
    role = user["role"]
    if role == "owner":
        return True
    if role == "admin":
        # Cannot delete root-level files (owner's files)
        if not _is_inside_homes(relative_path):
            return False
        # Cannot delete files belonging to the owner user
        path_owner = get_path_owner(relative_path)
        if path_owner:
            owner_user = get_owner_user()
            if owner_user and path_owner == owner_user["username"]:
                return False
        return True
    # Regular user: only own home
    path_owner = get_path_owner(relative_path)
    return path_owner == user["username"]


def check_permission(user: dict, action: str, relative_path: str):
    """Raise 403 if the user doesn't have permission for the action on the path."""
    if action == "read":
        allowed = can_read(user, relative_path)
    elif action in ("write", "upload", "mkdir", "rename"):
        allowed = can_write(user, relative_path)
    elif action == "delete":
        allowed = can_delete(user, relative_path)
    else:
        allowed = False
    if not allowed:
        raise HTTPException(status_code=403, detail="Permission denied")


def get_user_root(user: dict) -> str:
    """Get the filesystem root path for a user's view.

    Owner/admin see real root '/'.
    Regular users see their home '/_homes/{username}'.
    """
    if user["role"] in ("owner", "admin"):
        return "/"
    return f"/{config.HOMES_DIR}/{user['username']}"


def remap_path_for_user(path: str, user: dict) -> str:
    """Remap a path for regular users.

    Regular users' '/' maps to '/_homes/{username}/'.
    Owner/admin paths pass through unchanged.
    """
    if user["role"] in ("owner", "admin"):
        return path

    home_prefix = f"/{config.HOMES_DIR}/{user['username']}"
    if path == "/" or not path:
        return home_prefix
    # If path doesn't start with the home prefix, prepend it
    if not path.startswith(home_prefix):
        return home_prefix + "/" + path.lstrip("/")
    return path


def resolve_and_authorize(user: dict, user_path: str, action: str) -> tuple[str, str]:
    """Canonicalize a user-supplied path and authorize it at the resolved location.

    Closes three classes of bug that bit us before:
      1. Symlink pivot: permission on the *unresolved* path is insufficient — a
         symlink inside the user's home pointing at another user's file would
         let them read/write the target. We realpath first, then re-derive the
         canonical relative path, then authorize again.
      2. Hidden .trash reads: /download, /stream, /text previously bypassed
         the trash guard that list/upload/etc had. The check happens here so
         every caller gets it for free.
      3. Forgotten checks: making this a single helper means one place to add
         future scope checks.

    Returns (absolute_path, canonical_relative_path).
    Raises HTTPException on path traversal or permission failure.
    """
    from .services import filesystem as fs

    if action not in _VALID_ACTIONS:
        raise HTTPException(status_code=500, detail=f"Unknown action: {action}")

    remapped = remap_path_for_user(user_path, user)
    # First-line check on the user-supplied (remapped) path. This catches the
    # obvious cases cheaply; the re-check below handles symlink pivots.
    check_permission(user, action, remapped)
    if fs.is_trash_path(remapped):
        raise HTTPException(status_code=403, detail="Trash is not accessible here")

    try:
        abs_path = fs.safe_resolve(remapped)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")

    # Re-derive the canonical relative path from the realpath result and
    # authorize again. safe_resolve already ensures abs_path is within
    # FILE_ROOT, but not that it's within *this user's* scope.
    canonical_rel = fs.relative_path(abs_path)
    if fs.is_trash_path(canonical_rel):
        raise HTTPException(status_code=403, detail="Trash is not accessible here")
    check_permission(user, action, canonical_rel)
    return abs_path, canonical_rel


def resolve_parent_and_authorize(user: dict, user_path: str, action: str) -> tuple[str, str]:
    """Like resolve_and_authorize, but for operations that create a new child.

    The target itself doesn't exist yet (mkdir, chunk upload destination). We
    authorize the parent directory — which *must* exist and be writable —
    then return the would-be absolute path of the new child.
    """
    from .services import filesystem as fs

    remapped = remap_path_for_user(user_path, user)
    parent_rel = remapped.rsplit("/", 1)[0] or "/"
    # Authorize the parent at creation time. If parent contains a symlink
    # pivot, the resolved parent will fall outside the user's scope and
    # the second check below rejects.
    check_permission(user, action, parent_rel)
    if fs.is_trash_path(parent_rel):
        raise HTTPException(status_code=403, detail="Trash is not accessible here")

    try:
        parent_abs = fs.safe_resolve(parent_rel)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")

    canonical_parent_rel = fs.relative_path(parent_abs)
    if fs.is_trash_path(canonical_parent_rel):
        raise HTTPException(status_code=403, detail="Trash is not accessible here")
    check_permission(user, action, canonical_parent_rel)

    import os as _os
    child_name = _os.path.basename(remapped.rstrip("/"))
    if not child_name or "/" in child_name or "\0" in child_name:
        raise HTTPException(status_code=400, detail="Invalid name")
    target_abs = _os.path.join(parent_abs, child_name)
    if canonical_parent_rel == "/":
        target_rel = "/" + child_name
    else:
        target_rel = canonical_parent_rel.rstrip("/") + "/" + child_name
    return target_abs, target_rel
