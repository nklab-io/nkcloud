from fastapi import HTTPException

from . import config
from .database import get_owner_user


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
