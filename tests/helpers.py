import os
import uuid
import zipfile
from contextlib import contextmanager
from io import BytesIO

from fastapi.testclient import TestClient


DEFAULT_PASSWORD = "password123"


def csrf_headers(client: TestClient) -> dict[str, str]:
    token = client.cookies.get("nkcloud_csrf")
    return {"X-CSRF-Token": token or ""}


def setup_owner(client: TestClient, username: str = "owner", password: str = DEFAULT_PASSWORD):
    res = client.post("/api/setup", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    return res


def create_user(env, username: str, role: str = "user", password: str = DEFAULT_PASSWORD,
                quota_bytes: int = 0):
    user_id = str(uuid.uuid4())
    pw_hash = env.main.hash_password(password)
    with env.main.get_db() as conn:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, role, quota_bytes) "
            "VALUES (?,?,?,?,?)",
            (user_id, username, pw_hash, role, quota_bytes),
        )
        conn.commit()
    home = env.file_root / env.config.HOMES_DIR / username
    home.mkdir(parents=True, exist_ok=True)
    return {"id": user_id, "username": username, "role": role, "home": home}


def set_used_bytes(env, username: str, used_bytes: int):
    with env.main.get_db() as conn:
        conn.execute("UPDATE users SET used_bytes = ? WHERE username = ?", (used_bytes, username))
        conn.commit()


def get_used_bytes(env, username: str) -> int:
    with env.main.get_db() as conn:
        row = conn.execute("SELECT used_bytes FROM users WHERE username = ?", (username,)).fetchone()
    return int(row[0])


@contextmanager
def logged_in_client(env, username: str, password: str = DEFAULT_PASSWORD):
    with TestClient(env.app) as client:
        res = client.post("/api/login", json={"username": username, "password": password})
        assert res.status_code == 200, res.text
        yield client


def write_file(path, content: bytes | str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(content, bytes) else "w"
    with open(path, mode) as f:
        f.write(content)


def zip_names(response) -> set[str]:
    with zipfile.ZipFile(BytesIO(response.content)) as zf:
        return set(zf.namelist())
