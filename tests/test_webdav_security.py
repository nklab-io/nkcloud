import os

import pytest

from tests.helpers import create_user


requires_symlink = pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink unavailable")


def _webdav():
    from app import webdav
    return webdav


def _gate_call(env, environ):
    calls = []

    def next_app(next_environ, start_response):
        calls.append(next_environ)
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"ok"]

    statuses = []

    def start_response(status, headers):
        statuses.append(status)

    gate = _webdav().GateMiddleware(None, next_app, {})
    body = b"".join(gate(environ, start_response))
    return statuses[0], body, calls


def test_webdav_user_provider_roots_regular_user_to_home(nkcloud_env):
    alice = create_user(nkcloud_env, "alice")
    provider = _webdav().PerUserFilesystemProvider(str(nkcloud_env.file_root))

    resolved = provider._loc_to_file_path("/docs/file.txt", {"nkcloud.user": alice})

    assert resolved == str(alice["home"] / "docs" / "file.txt")


def test_webdav_user_provider_blocks_path_traversal(nkcloud_env):
    alice = create_user(nkcloud_env, "alice")
    provider = _webdav().PerUserFilesystemProvider(str(nkcloud_env.file_root))

    with pytest.raises(Exception):
        provider._loc_to_file_path("/../owner-secret.txt", {"nkcloud.user": alice})


@requires_symlink
def test_webdav_user_provider_blocks_symlink_escape(nkcloud_env):
    alice = create_user(nkcloud_env, "alice")
    outside = nkcloud_env.file_root.parent / "outside.txt"
    outside.write_text("secret")
    os.symlink(outside, alice["home"] / "leak.txt")
    provider = _webdav().PerUserFilesystemProvider(str(nkcloud_env.file_root))

    with pytest.raises(Exception):
        provider._loc_to_file_path("/leak.txt", {"nkcloud.user": alice})


def test_webdav_owner_provider_uses_file_root(nkcloud_env):
    owner = {"username": "owner", "role": "owner"}
    provider = _webdav().PerUserFilesystemProvider(str(nkcloud_env.file_root))

    resolved = provider._loc_to_file_path("/root.txt", {"nkcloud.user": owner})

    assert resolved == str(nkcloud_env.file_root / "root.txt")


def test_webdav_gate_blocks_trash_path(nkcloud_env):
    alice = {"username": "alice", "role": "user"}

    status, body, calls = _gate_call(nkcloud_env, {
        "nkcloud.user": alice,
        "PATH_INFO": "/.trash/item",
        "REQUEST_METHOD": "GET",
    })

    assert status.startswith("403")
    assert b"Trash" in body
    assert calls == []


def test_webdav_gate_blocks_trash_destination(nkcloud_env):
    alice = {"username": "alice", "role": "user"}

    status, body, calls = _gate_call(nkcloud_env, {
        "nkcloud.user": alice,
        "PATH_INFO": "/file.txt",
        "REQUEST_METHOD": "MOVE",
        "HTTP_DESTINATION": "http://example.test/.trash/file.txt",
    })

    assert status.startswith("403")
    assert b"Trash" in body
    assert calls == []


def test_webdav_gate_blocks_admin_write_outside_homes(nkcloud_env):
    admin = {"username": "admin1", "role": "admin"}

    status, body, calls = _gate_call(nkcloud_env, {
        "nkcloud.user": admin,
        "PATH_INFO": "/root.txt",
        "REQUEST_METHOD": "PUT",
    })

    assert status.startswith("403")
    assert b"Admin write" in body
    assert calls == []


def test_webdav_gate_blocks_admin_destination_outside_homes(nkcloud_env):
    admin = {"username": "admin1", "role": "admin"}

    status, body, calls = _gate_call(nkcloud_env, {
        "nkcloud.user": admin,
        "PATH_INFO": f"/{nkcloud_env.config.HOMES_DIR}/alice/file.txt",
        "REQUEST_METHOD": "MOVE",
        "HTTP_DESTINATION": "http://example.test/root.txt",
    })

    assert status.startswith("403")
    assert b"Admin write" in body
    assert calls == []


def test_webdav_gate_allows_admin_write_inside_homes(nkcloud_env):
    admin = {"username": "admin1", "role": "admin"}

    status, body, calls = _gate_call(nkcloud_env, {
        "nkcloud.user": admin,
        "PATH_INFO": f"/{nkcloud_env.config.HOMES_DIR}/alice/file.txt",
        "REQUEST_METHOD": "PUT",
    })

    assert status.startswith("200")
    assert body == b"ok"
    assert len(calls) == 1
