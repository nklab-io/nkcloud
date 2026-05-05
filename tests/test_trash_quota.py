import json

from fastapi.testclient import TestClient

from tests.helpers import (
    csrf_headers,
    create_user,
    get_used_bytes,
    logged_in_client,
    set_used_bytes,
    setup_owner,
    write_file,
)


def _trash_entry(client, path: str):
    res = client.get("/api/files/trash")
    assert res.status_code == 200, res.text
    for entry in res.json()["entries"]:
        if entry["orig_path"] == path:
            return entry
    raise AssertionError(f"trash entry not found for {path}: {res.text}")


def _delete_file(client, path: str):
    res = client.request(
        "DELETE",
        "/api/files",
        json={"paths": [path]},
        headers=csrf_headers(client),
    )
    assert res.status_code == 200, res.text
    assert res.json()["deleted"] == [path]
    return res


def test_soft_delete_keeps_used_bytes_until_purge(nkcloud_env):
    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
    alice = create_user(nkcloud_env, "alice")
    write_file(alice["home"] / "old.txt", "12345")
    set_used_bytes(nkcloud_env, "alice", 5)

    with logged_in_client(nkcloud_env, "alice") as client:
        _delete_file(client, "/old.txt")
        entry = _trash_entry(client, "/old.txt")

        assert not (alice["home"] / "old.txt").exists()
        assert get_used_bytes(nkcloud_env, "alice") == 5

        purge = client.request(
            "DELETE",
            "/api/files/trash",
            json={"ids": [entry["id"]]},
            headers=csrf_headers(client),
        )

    assert purge.status_code == 200, purge.text
    assert purge.json()["purged"] == [entry["id"]]
    assert get_used_bytes(nkcloud_env, "alice") == 0


def test_empty_trash_deducts_all_freed_bytes(nkcloud_env):
    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
    alice = create_user(nkcloud_env, "alice")
    write_file(alice["home"] / "a.txt", "12")
    write_file(alice["home"] / "b.txt", "345")
    set_used_bytes(nkcloud_env, "alice", 5)

    with logged_in_client(nkcloud_env, "alice") as client:
        _delete_file(client, "/a.txt")
        _delete_file(client, "/b.txt")
        empty = client.post("/api/files/trash/empty", headers=csrf_headers(client))

    assert empty.status_code == 200, empty.text
    assert empty.json()["purged_count"] == 2
    assert get_used_bytes(nkcloud_env, "alice") == 0


def test_restore_keeps_used_bytes_and_renames_on_collision(nkcloud_env):
    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
    alice = create_user(nkcloud_env, "alice")
    write_file(alice["home"] / "report.txt", "old")
    set_used_bytes(nkcloud_env, "alice", 3)

    with logged_in_client(nkcloud_env, "alice") as client:
        _delete_file(client, "/report.txt")
        entry = _trash_entry(client, "/report.txt")
        write_file(alice["home"] / "report.txt", "new")

        restore = client.post(
            "/api/files/trash/restore",
            json={"ids": [entry["id"]]},
            headers=csrf_headers(client),
        )

    assert restore.status_code == 200, restore.text
    assert restore.json()["restored"] == [entry["id"]]
    assert (alice["home"] / "report.txt").read_text() == "new"
    assert (alice["home"] / "report (restored 1).txt").read_text() == "old"
    assert get_used_bytes(nkcloud_env, "alice") == 3


def test_restore_rejects_tampered_destination_outside_user_scope(nkcloud_env):
    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
    alice = create_user(nkcloud_env, "alice")
    write_file(alice["home"] / "note.txt", "note")
    set_used_bytes(nkcloud_env, "alice", 4)

    with logged_in_client(nkcloud_env, "alice") as client:
        _delete_file(client, "/note.txt")
        entry = _trash_entry(client, "/note.txt")

        index_path = alice["home"] / ".trash" / ".index.json"
        index = json.loads(index_path.read_text())
        index[entry["id"]]["orig_path"] = "/owner-only.txt"
        index_path.write_text(json.dumps(index))

        restore = client.post(
            "/api/files/trash/restore",
            json={"ids": [entry["id"]]},
            headers=csrf_headers(client),
        )

    assert restore.status_code == 200, restore.text
    assert restore.json()["restored"] == []
    assert restore.json()["failed"] == [{"id": entry["id"], "reason": "forbidden"}]
    assert not (nkcloud_env.file_root / "owner-only.txt").exists()
    assert get_used_bytes(nkcloud_env, "alice") == 4


def test_delete_user_home_root_is_protected(nkcloud_env):
    with TestClient(nkcloud_env.app) as owner:
        setup_owner(owner)
    alice = create_user(nkcloud_env, "alice")

    with logged_in_client(nkcloud_env, "alice") as client:
        res = client.request(
            "DELETE",
            "/api/files",
            json={"paths": ["/"]},
            headers=csrf_headers(client),
        )

    assert res.status_code == 200, res.text
    assert res.json()["deleted"] == []
    assert res.json()["failed"] == [{"path": "/", "reason": "root_protected"}]
    assert alice["home"].exists()
