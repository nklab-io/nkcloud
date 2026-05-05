from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from tests.helpers import (
    csrf_headers,
    create_user,
    logged_in_client,
    setup_owner,
    write_file,
    zip_names,
)


def _create_share(client, path: str, **extra):
    payload = {"path": path, "type": extra.pop("type", "file_download")}
    payload.update(extra)
    res = client.post("/api/shares", json=payload, headers=csrf_headers(client))
    assert res.status_code == 200, res.text
    return res.json()


def _share_download_count(env, share_id: str) -> int:
    with env.main.get_db() as conn:
        row = conn.execute("SELECT download_count FROM shares WHERE id = ?", (share_id,)).fetchone()
    return int(row[0])


def test_password_share_hides_metadata_and_requires_verify_for_download(nkcloud_env):
    secret = nkcloud_env.file_root / "secret.txt"
    write_file(secret, "classified")

    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
        share = _create_share(owner, "/secret.txt", password="sharepass")

    with TestClient(nkcloud_env.app) as public:
        info = public.get(f"/api/public/{share['token']}")
        assert info.status_code == 200, info.text
        assert info.json()["needs_password"] is True
        assert "size" not in info.json()

        denied = public.get(f"/api/public/{share['token']}/download")
        assert denied.status_code == 401

        wrong = public.post(f"/api/public/{share['token']}/verify", json={"password": "wrong"})
        assert wrong.status_code == 401

        verified = public.post(f"/api/public/{share['token']}/verify", json={"password": "sharepass"})
        assert verified.status_code == 200, verified.text
        assert verified.json()["valid"] is True

        download = public.get(f"/api/public/{share['token']}/download")

    assert download.status_code == 200, download.text
    assert download.content == b"classified"
    assert _share_download_count(nkcloud_env, share["id"]) == 1


def test_expired_share_rejects_info_verify_and_download(nkcloud_env):
    write_file(nkcloud_env.file_root / "expired.txt", "old")
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
        share = _create_share(owner, "/expired.txt", expires_at=past, password="sharepass")

    with TestClient(nkcloud_env.app) as public:
        info = public.get(f"/api/public/{share['token']}")
        verify = public.post(f"/api/public/{share['token']}/verify", json={"password": "sharepass"})
        download = public.get(f"/api/public/{share['token']}/download")

    assert info.status_code == 410
    assert verify.status_code == 410
    assert download.status_code == 410


def test_browse_share_requires_login_for_info_and_download(nkcloud_env):
    write_file(nkcloud_env.file_root / "browse.txt", "content")

    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
        share = _create_share(owner, "/browse.txt", type="browse")

    with TestClient(nkcloud_env.app) as public:
        info = public.get(f"/api/public/{share['token']}")
        download = public.get(f"/api/public/{share['token']}/download")

    assert info.status_code == 401
    assert download.status_code == 401

    with logged_in_client(nkcloud_env, "owner") as authenticated:
        info_ok = authenticated.get(f"/api/public/{share['token']}")
        download_ok = authenticated.get(f"/api/public/{share['token']}/download")

    assert info_ok.status_code == 200, info_ok.text
    assert download_ok.status_code == 200, download_ok.text
    assert download_ok.content == b"content"


def test_public_directory_download_path_must_stay_inside_share_root(nkcloud_env):
    shared = nkcloud_env.file_root / "shared"
    write_file(shared / "visible.txt", "visible")
    write_file(nkcloud_env.file_root / "secret.txt", "secret")

    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
        share = _create_share(owner, "/shared")

    with TestClient(nkcloud_env.app) as public:
        ok = public.get(f"/api/public/{share['token']}/download", params={"path": "visible.txt"})
        blocked = public.get(f"/api/public/{share['token']}/download", params={"path": "../secret.txt"})

    assert ok.status_code == 200, ok.text
    assert ok.content == b"visible"
    assert blocked.status_code == 400
    assert _share_download_count(nkcloud_env, share["id"]) == 1


def test_public_directory_zip_download_count_increments_once(nkcloud_env):
    shared = nkcloud_env.file_root / "folder"
    write_file(shared / "a.txt", "a")
    write_file(shared / "b.txt", "b")

    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
        share = _create_share(owner, "/folder")

    with TestClient(nkcloud_env.app) as public:
        res = public.get(f"/api/public/{share['token']}/download")

    assert res.status_code == 200, res.text
    assert zip_names(res) == {"folder/a.txt", "folder/b.txt"}
    assert _share_download_count(nkcloud_env, share["id"]) == 1


def test_member_can_only_delete_own_share(nkcloud_env):
    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
    alice = create_user(nkcloud_env, "alice")
    bob = create_user(nkcloud_env, "bob")
    write_file(alice["home"] / "alice.txt", "a")
    write_file(bob["home"] / "bob.txt", "b")

    with logged_in_client(nkcloud_env, "alice") as alice_client:
        alice_share = _create_share(alice_client, "/alice.txt")

    with logged_in_client(nkcloud_env, "bob") as bob_client:
        bob_share = _create_share(bob_client, "/bob.txt")
        denied = bob_client.delete(f"/api/shares/{alice_share['id']}", headers=csrf_headers(bob_client))
        deleted = bob_client.delete(f"/api/shares/{bob_share['id']}", headers=csrf_headers(bob_client))

    assert denied.status_code == 403
    assert deleted.status_code == 200, deleted.text
