import os

import pytest
from fastapi.testclient import TestClient

from tests.helpers import (
    csrf_headers,
    create_user,
    logged_in_client,
    setup_owner,
    write_file,
    zip_names,
)


requires_symlink = pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink unavailable")


def test_setup_rejects_short_password(nkcloud_env):
    with TestClient(nkcloud_env.app) as client:
        res = client.post("/api/setup", json={"username": "owner", "password": "12345"})

    assert res.status_code == 400
    assert "Password" in res.json()["detail"]


def test_invite_register_rejects_short_password(nkcloud_env):
    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
        invite = owner.post("/api/invites", json={}, headers=csrf_headers(owner))
        assert invite.status_code == 200, invite.text
        token = invite.json()["token"]

    with TestClient(nkcloud_env.app) as anon:
        res = anon.post(
            f"/api/invite/{token}",
            json={"username": "alice", "password": "12345"},
        )

    assert res.status_code == 400
    assert "Password" in res.json()["detail"]


def test_csrf_required_for_mutating_file_api(nkcloud_env):
    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
        res = owner.post("/api/files/mkdir", json={"path": "/blocked"})

    assert res.status_code == 403
    assert res.json()["detail"] == "CSRF token invalid"


@requires_symlink
def test_file_listing_hides_symlink_to_outside_scope(nkcloud_env):
    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
    alice = create_user(nkcloud_env, "alice")
    write_file(alice["home"] / "visible.txt", "ok")
    write_file(nkcloud_env.file_root / "secret.txt", "secret")
    os.symlink(nkcloud_env.file_root / "secret.txt", alice["home"] / "leak.txt")

    with logged_in_client(nkcloud_env, "alice") as client:
        res = client.get("/api/files", params={"path": "/"})

    assert res.status_code == 200, res.text
    names = {item["name"] for item in res.json()["items"]}
    assert "visible.txt" in names
    assert "leak.txt" not in names


@requires_symlink
def test_search_does_not_traverse_symlink_directory(nkcloud_env):
    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
    alice = create_user(nkcloud_env, "alice")
    outside_dir = nkcloud_env.file_root / "outside"
    write_file(outside_dir / "needle.txt", "secret")
    os.symlink(outside_dir, alice["home"] / "outside-link")

    with logged_in_client(nkcloud_env, "alice") as client:
        res = client.get("/api/search", params={"q": "needle", "path": "/"})

    assert res.status_code == 200, res.text
    assert res.json()["results"] == []


@requires_symlink
def test_user_folder_zip_excludes_symlink_to_outside_scope(nkcloud_env):
    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
    alice = create_user(nkcloud_env, "alice")
    write_file(alice["home"] / "visible.txt", "ok")
    write_file(nkcloud_env.file_root / "secret.txt", "secret")
    os.symlink(nkcloud_env.file_root / "secret.txt", alice["home"] / "leak.txt")

    with logged_in_client(nkcloud_env, "alice") as client:
        res = client.get("/api/files/download-zip", params={"path": "/"})

    assert res.status_code == 200, res.text
    names = zip_names(res)
    assert "alice/visible.txt" in names
    assert "alice/leak.txt" not in names


@requires_symlink
def test_public_share_zip_excludes_symlink_to_outside_shared_folder(nkcloud_env):
    shared = nkcloud_env.file_root / "shared"
    write_file(shared / "visible.txt", "ok")
    write_file(nkcloud_env.file_root / "secret.txt", "secret")
    os.symlink(nkcloud_env.file_root / "secret.txt", shared / "leak.txt")

    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
        share = owner.post(
            "/api/shares",
            json={"path": "/shared", "type": "file_download"},
            headers=csrf_headers(owner),
        )
        assert share.status_code == 200, share.text
        token = share.json()["token"]

    with TestClient(nkcloud_env.app) as public:
        res = public.get(f"/api/public/{token}/download")

    assert res.status_code == 200, res.text
    names = zip_names(res)
    assert "shared/visible.txt" in names
    assert "shared/leak.txt" not in names


def test_chunk_complete_missing_chunk_cleans_session_and_chunks(nkcloud_env):
    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
    alice = create_user(nkcloud_env, "alice")

    with logged_in_client(nkcloud_env, "alice") as client:
        init = client.post(
            "/api/files/upload/init",
            data={"path": "/", "filename": "big.bin", "total_bytes": "10", "total_chunks": "2"},
            headers=csrf_headers(client),
        )
        assert init.status_code == 200, init.text
        upload_id = init.json()["upload_id"]

        chunk = client.post(
            "/api/files/upload/chunk",
            data={"upload_id": upload_id, "chunk_index": "0", "total_chunks": "2", "filename": "big.bin"},
            files={"file": ("chunk-0", b"12345", "application/octet-stream")},
            headers=csrf_headers(client),
        )
        assert chunk.status_code == 200, chunk.text

        complete = client.post(
            "/api/files/upload/complete",
            data={"upload_id": upload_id, "total_chunks": "2", "filename": "big.bin", "path": "/"},
            headers=csrf_headers(client),
        )

    assert complete.status_code == 400
    assert not (nkcloud_env.chunk_dir / upload_id).exists()
    assert not (alice["home"] / "big.bin").exists()
    with nkcloud_env.main.get_db() as conn:
        row = conn.execute("SELECT upload_id FROM upload_sessions WHERE upload_id = ?", (upload_id,)).fetchone()
    assert row is None


def test_upload_init_respects_reserved_quota(nkcloud_env):
    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
    create_user(nkcloud_env, "alice", quota_bytes=5)

    with logged_in_client(nkcloud_env, "alice") as client:
        res = client.post(
            "/api/files/upload/init",
            data={"path": "/", "filename": "too-big.bin", "total_bytes": "6", "total_chunks": "1"},
            headers=csrf_headers(client),
        )

    assert res.status_code == 413
    with nkcloud_env.main.get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM upload_sessions").fetchone()[0]
    assert count == 0


def test_successful_chunk_upload_updates_file_and_used_bytes(nkcloud_env):
    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
    alice = create_user(nkcloud_env, "alice", quota_bytes=100)

    with logged_in_client(nkcloud_env, "alice") as client:
        init = client.post(
            "/api/files/upload/init",
            data={"path": "/", "filename": "ok.bin", "total_bytes": "5", "total_chunks": "1"},
            headers=csrf_headers(client),
        )
        assert init.status_code == 200, init.text
        upload_id = init.json()["upload_id"]

        chunk = client.post(
            "/api/files/upload/chunk",
            data={"upload_id": upload_id, "chunk_index": "0", "total_chunks": "1", "filename": "ok.bin"},
            files={"file": ("chunk-0", b"abcde", "application/octet-stream")},
            headers=csrf_headers(client),
        )
        assert chunk.status_code == 200, chunk.text

        complete = client.post(
            "/api/files/upload/complete",
            data={"upload_id": upload_id, "total_chunks": "1", "filename": "ok.bin", "path": "/"},
            headers=csrf_headers(client),
        )

    assert complete.status_code == 200, complete.text
    assert (alice["home"] / "ok.bin").read_bytes() == b"abcde"
    with nkcloud_env.main.get_db() as conn:
        used = conn.execute("SELECT used_bytes FROM users WHERE username = ?", ("alice",)).fetchone()[0]
    assert used == 5


def test_forwarded_ip_is_ignored_without_trust_proxy(nkcloud_env):
    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
        res = owner.get("/api/users/me", headers={"X-Forwarded-For": "203.0.113.9"})

    assert res.status_code == 200, res.text
    assert res.json()["current_ip"] != "203.0.113.9"
