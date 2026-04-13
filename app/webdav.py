import os
import threading

from wsgidav.wsgidav_app import WsgiDAVApp
from cheroot.wsgi import Server as CherootServer

from . import config
from .auth import verify_password
from .database import get_user_by_username, check_rate_limit, record_login_attempt


class NkCloudDomainController:
    """WsgiDAV domain controller using NkCloud's user database."""

    def __init__(self, wsgidav_app, config_opts):
        pass

    def get_domain_realm(self, path_info, environ):
        return "NkCloud"

    def require_authentication(self, realm, environ):
        return True

    def basic_auth_user(self, realm, user_name, password, environ):
        # Rate limit check (use remote addr)
        client_ip = environ.get("REMOTE_ADDR", "unknown")
        if check_rate_limit(client_ip):
            return False

        user = get_user_by_username(user_name)
        if not user:
            record_login_attempt(client_ip, success=False)
            return False
        if user["is_disabled"]:
            record_login_attempt(client_ip, success=False)
            return False
        if not verify_password(password, user["password_hash"]):
            record_login_attempt(client_ip, success=False)
            return False

        record_login_attempt(client_ip, success=True)
        # Store user info for path scoping middleware
        environ["nkcloud.user"] = user
        return True

    def supports_http_digest_auth(self):
        return False

    def digest_auth_user(self, *args, **kwargs):
        return False

    def is_share_anonymous(self, path_info):
        return False


class UserScopingMiddleware:
    """WSGI middleware that restricts WebDAV paths based on authenticated user."""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        user = environ.get("nkcloud.user")
        if not user:
            # No user info = auth hasn't happened yet, let wsgidav handle it
            return self.app(environ, start_response)

        path = environ.get("PATH_INFO", "/")
        role = user["role"]

        # Block access to .trash/ for everyone via WebDAV — the web UI owns
        # trash semantics (soft-delete, restore, retention). Letting WebDAV
        # poke .trash would expose UUID-named files and bypass the recovery
        # window if a client deletes from there.
        if config.TRASH_DIR in path.strip("/").split("/"):
            start_response("403 Forbidden", [("Content-Type", "text/plain")])
            return [b"Trash is managed via the web UI"]

        if role == "owner":
            # Owner sees everything
            return self.app(environ, start_response)

        if role == "user":
            # Regular users must stay within /_homes/{username}/
            allowed_prefix = f"/{config.HOMES_DIR}/{user['username']}"
            if path == "/" or not (path == allowed_prefix or path.startswith(allowed_prefix + "/")):
                start_response("403 Forbidden", [("Content-Type", "text/plain")])
                return [b"Access denied"]

        if role == "admin":
            # Admin can read anything, write only inside _homes/
            method = environ.get("REQUEST_METHOD", "GET").upper()
            if method in ("PUT", "MKCOL", "DELETE", "MOVE", "COPY", "PROPPATCH"):
                # Must be inside _homes/
                if not path.startswith(f"/{config.HOMES_DIR}/"):
                    start_response("403 Forbidden", [("Content-Type", "text/plain")])
                    return [b"Access denied - read only"]

        return self.app(environ, start_response)


def create_webdav_app():
    dav_config = {
        "provider_mapping": {
            "/": config.FILE_ROOT,
        },
        "http_authenticator": {
            "domain_controller": NkCloudDomainController,
            "accept_basic": True,
            "accept_digest": False,
            "default_to_digest": False,
        },
        "verbose": 1,
        "logging": {
            "enable": True,
            "enable_loggers": [],
        },
        "dir_browser": {
            "enable": True,
            "response_trailer": "",
            "davmount": False,
        },
    }
    inner_app = WsgiDAVApp(dav_config)
    return UserScopingMiddleware(inner_app)


def start_webdav_server():
    """Start WebDAV server in a background thread."""
    app = create_webdav_app()
    server = CherootServer(("0.0.0.0", 8001), app)
    thread = threading.Thread(target=server.start, daemon=True)
    thread.start()
    return server
