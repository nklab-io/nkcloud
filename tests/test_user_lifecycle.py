from fastapi.testclient import TestClient

from tests.helpers import csrf_headers, create_user, logged_in_client, setup_owner, write_file


def _owner_id(client) -> str:
    res = client.get("/api/users/me")
    assert res.status_code == 200, res.text
    return res.json()["id"]


def test_admin_and_member_cannot_manage_users(nkcloud_env):
    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
    admin = create_user(nkcloud_env, "admin1", role="admin")
    member = create_user(nkcloud_env, "alice")

    with logged_in_client(nkcloud_env, "admin1") as admin_client:
        list_denied = admin_client.get("/api/users")
        update_denied = admin_client.put(
            f"/api/users/{member['id']}",
            json={"quota_bytes": 10},
            headers=csrf_headers(admin_client),
        )
        delete_denied = admin_client.delete(
            f"/api/users/{member['id']}",
            headers=csrf_headers(admin_client),
        )

    with logged_in_client(nkcloud_env, "alice") as member_client:
        member_list_denied = member_client.get("/api/users")
        invite_denied = member_client.post("/api/invites", json={}, headers=csrf_headers(member_client))

    assert list_denied.status_code == 403
    assert update_denied.status_code == 403
    assert delete_denied.status_code == 403
    assert member_list_denied.status_code == 403
    assert invite_denied.status_code == 403
    assert admin["role"] == "admin"


def test_owner_cannot_modify_or_delete_self(nkcloud_env):
    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
        owner_id = _owner_id(owner)

        update = owner.put(
            f"/api/users/{owner_id}",
            json={"role": "user"},
            headers=csrf_headers(owner),
        )
        delete = owner.delete(f"/api/users/{owner_id}", headers=csrf_headers(owner))

    assert update.status_code == 400
    assert delete.status_code == 400


def test_owner_can_update_member_role_quota_and_disable_login(nkcloud_env):
    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
    alice = create_user(nkcloud_env, "alice")

    with TestClient(nkcloud_env.app) as owner:
        login = owner.post("/api/login", json={"username": "owner", "password": "password123"})
        assert login.status_code == 200, login.text
        update = owner.put(
            f"/api/users/{alice['id']}",
            json={"role": "admin", "quota_bytes": 1024, "is_disabled": True},
            headers=csrf_headers(owner),
        )

    assert update.status_code == 200, update.text
    with nkcloud_env.main.get_db() as conn:
        row = conn.execute(
            "SELECT role, quota_bytes, is_disabled FROM users WHERE id = ?",
            (alice["id"],),
        ).fetchone()
    assert dict(row) == {"role": "admin", "quota_bytes": 1024, "is_disabled": 1}

    with TestClient(nkcloud_env.app) as disabled_client:
        disabled_login = disabled_client.post(
            "/api/login",
            json={"username": "alice", "password": "password123"},
        )
    assert disabled_login.status_code == 401


def test_owner_deleting_user_removes_shares_and_optionally_files(nkcloud_env):
    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
    alice = create_user(nkcloud_env, "alice")
    write_file(alice["home"] / "doc.txt", "doc")

    with logged_in_client(nkcloud_env, "alice") as alice_client:
        share = alice_client.post(
            "/api/shares",
            json={"path": "/doc.txt", "type": "file_download"},
            headers=csrf_headers(alice_client),
        )
        assert share.status_code == 200, share.text

    with TestClient(nkcloud_env.app) as owner:
        login = owner.post("/api/login", json={"username": "owner", "password": "password123"})
        assert login.status_code == 200, login.text
        delete = owner.delete(
            f"/api/users/{alice['id']}",
            params={"delete_files": "true"},
            headers=csrf_headers(owner),
        )

    assert delete.status_code == 200, delete.text
    assert not alice["home"].exists()
    with nkcloud_env.main.get_db() as conn:
        user_count = conn.execute("SELECT COUNT(*) FROM users WHERE id = ?", (alice["id"],)).fetchone()[0]
        share_count = conn.execute("SELECT COUNT(*) FROM shares WHERE created_by = ?", (alice["id"],)).fetchone()[0]
    assert user_count == 0
    assert share_count == 0


def test_owner_deleting_user_without_files_keeps_home(nkcloud_env):
    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
    alice = create_user(nkcloud_env, "alice")
    write_file(alice["home"] / "doc.txt", "doc")

    with TestClient(nkcloud_env.app) as owner:
        login = owner.post("/api/login", json={"username": "owner", "password": "password123"})
        assert login.status_code == 200, login.text
        delete = owner.delete(f"/api/users/{alice['id']}", headers=csrf_headers(owner))

    assert delete.status_code == 200, delete.text
    assert (alice["home"] / "doc.txt").exists()


def test_admin_can_create_invite_but_not_list_users(nkcloud_env):
    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
    create_user(nkcloud_env, "admin1", role="admin")

    with logged_in_client(nkcloud_env, "admin1") as admin_client:
        invite = admin_client.post("/api/invites", json={}, headers=csrf_headers(admin_client))
        users = admin_client.get("/api/users")

    assert invite.status_code == 200, invite.text
    assert invite.json()["token"]
    assert users.status_code == 403


def test_deleted_user_cannot_login(nkcloud_env):
    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
    alice = create_user(nkcloud_env, "alice")

    with TestClient(nkcloud_env.app) as owner:
        login = owner.post("/api/login", json={"username": "owner", "password": "password123"})
        assert login.status_code == 200, login.text
        delete = owner.delete(f"/api/users/{alice['id']}", headers=csrf_headers(owner))
        assert delete.status_code == 200, delete.text

    with TestClient(nkcloud_env.app) as client:
        login = client.post("/api/login", json={"username": "alice", "password": "password123"})

    assert login.status_code == 401
