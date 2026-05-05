import sys
from dataclasses import dataclass
from pathlib import Path

import pytest


@dataclass
class NkCloudTestEnv:
    app: object
    main: object
    config: object
    file_root: Path
    data_dir: Path
    chunk_dir: Path


def _clear_app_modules():
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name)


@pytest.fixture()
def nkcloud_env(tmp_path, monkeypatch):
    file_root = tmp_path / "files"
    data_dir = tmp_path / "data"
    chunk_dir = data_dir / "chunks"
    thumb_dir = data_dir / "thumbs"
    db_path = data_dir / "nkcloud.db"

    monkeypatch.setenv("NKCLOUD_FILE_ROOT", str(file_root))
    monkeypatch.setenv("NKCLOUD_DATA_DIR", str(data_dir))
    monkeypatch.setenv("NKCLOUD_DB_PATH", str(db_path))
    monkeypatch.setenv("NKCLOUD_CHUNK_DIR", str(chunk_dir))
    monkeypatch.setenv("NKCLOUD_THUMB_DIR", str(thumb_dir))
    monkeypatch.setenv("NKCLOUD_SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("NKCLOUD_COOKIE_SECURE", "0")
    monkeypatch.setenv("NKCLOUD_DISABLE_WEBDAV", "1")
    monkeypatch.delenv("NKCLOUD_TRUST_PROXY", raising=False)

    _clear_app_modules()
    import app.main as main

    yield NkCloudTestEnv(
        app=main.app,
        main=main,
        config=main.config,
        file_root=file_root,
        data_dir=data_dir,
        chunk_dir=chunk_dir,
    )

    _clear_app_modules()
