import os
import threading
from urllib.parse import urlparse, unquote

from wsgidav.wsgidav_app import WsgiDAVApp
from wsgidav.fs_dav_provider import FilesystemProvider
from wsgidav.dav_error import DAVError, HTTP_FORBIDDEN
from wsgidav.mw.base_mw import BaseMiddleware
from wsgidav.mw.cors import Cors
from wsgidav.error_printer import ErrorPrinter
from wsgidav.http_authenticator import HTTPAuthenticator
from wsgidav.dir_browser import WsgiDavDirBrowser
from wsgidav.request_resolver import RequestResolver
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
        client_ip = environ.get("REMOTE_ADDR", "unknown")
        if check_rate_limit(client_ip, username=user_name):
            return False

        user = get_user_by_username(user_name)
        ua = environ.get("HTTP_USER_AGENT", "")[:300]
        if not user or user["is_disabled"] or not verify_password(password, user["password_hash"]):
            record_login_attempt(client_ip, success=False, username=user_name, user_agent=ua)
            return False

        record_login_attempt(client_ip, success=True, username=user["username"], user_agent=ua)
        # Stashed for the per-user provider + write gate middleware.
        environ["nkcloud.user"] = user
        return True

    def supports_http_digest_auth(self):
        return False

    def digest_auth_user(self, *args, **kwargs):
        return False

    def is_share_anonymous(self, path_info):
        return False


class PerUserFilesystemProvider(FilesystemProvider):
    """FilesystemProvider that roots each non-owner user inside their home dir.

    This is the Option-B fix for the WebDAV permission model. Previously a
    single global "/" → FILE_ROOT mount relied on middleware to gate paths
    based on PATH_INFO — which missed MOVE/COPY Destination entirely, letting
    a regular user move their own files out to root or into .trash. By making
    the provider itself per-user-rooted, wsgidav's own Destination parsing
    runs through _loc_to_file_path and is contained by construction.
    """

    def _loc_to_file_path(self, path: str, environ: dict = None):
        user = (environ or {}).get("nkcloud.user")
        if user and user["role"] == "user":
            root = os.path.join(
                os.path.realpath(config.FILE_ROOT),
                config.HOMES_DIR,
                user["username"],
            )
            # Auto-create if missing so first-use WebDAV works.
            os.makedirs(root, exist_ok=True)
        else:
            root = os.path.realpath(config.FILE_ROOT)

        path_parts = path.strip("/").split("/")
        file_path = os.path.abspath(os.path.join(root, *path_parts))
        if file_path != root and not file_path.startswith(root + os.sep):
            raise DAVError(HTTP_FORBIDDEN)
        return file_path


def _path_contains_trash(path: str) -> bool:
    if not path:
        return False
    return config.TRASH_DIR in path.strip("/").split("/")


def _destination_path(environ) -> str | None:
    raw = environ.get("HTTP_DESTINATION")
    if not raw:
        return None
    try:
        return unquote(urlparse(raw).path)
    except Exception:
        return None


class GateMiddleware(BaseMiddleware):
    """Enforce the rules the per-user provider can't — runs AFTER auth.

    The original outer-wrapping middleware in v0.2.0 never fired for
    authenticated requests: wsgidav's HTTPAuthenticator sets
    `environ["nkcloud.user"]` *inside* WsgiDAVApp, so an outer middleware
    checking that key always saw nothing and passed through. Registering
    this class in `middleware_stack` right after HTTPAuthenticator makes
    it actually run post-auth.

    Handles:
      - .trash block (both PATH_INFO and HTTP_DESTINATION)
      - Admin mutating-method gate (write-only inside /_homes/)
    """

    MUTATING_METHODS = ("PUT", "MKCOL", "DELETE", "MOVE", "COPY", "PROPPATCH", "LOCK")

    def _deny(self, start_response, msg: bytes = b"Access denied"):
        start_response("403 Forbidden", [("Content-Type", "text/plain")])
        return [msg]

    def __call__(self, environ, start_response):
        user = environ.get("nkcloud.user")
        if not user:
            return self.next_app(environ, start_response)

        path = environ.get("PATH_INFO", "/")
        dest = _destination_path(environ)

        if _path_contains_trash(path) or (dest and _path_contains_trash(dest)):
            return self._deny(start_response, b"Trash is managed via the web UI")

        if user["role"] == "admin":
            method = environ.get("REQUEST_METHOD", "GET").upper()
            if method in self.MUTATING_METHODS:
                homes_prefix = f"/{config.HOMES_DIR}/"
                if not path.startswith(homes_prefix):
                    return self._deny(start_response, b"Admin write requires /_homes/")
                if dest and not dest.startswith(homes_prefix):
                    return self._deny(start_response, b"Admin write requires /_homes/")

        return self.next_app(environ, start_response)


def create_webdav_app():
    dav_config = {
        "provider_mapping": {
            "/": PerUserFilesystemProvider(os.path.realpath(config.FILE_ROOT)),
        },
        "http_authenticator": {
            "domain_controller": NkCloudDomainController,
            "accept_basic": True,
            "accept_digest": False,
            "default_to_digest": False,
        },
        # Insert GateMiddleware right after HTTPAuthenticator so it sees the
        # authenticated user. The rest of the stack is the wsgidav default.
        "middleware_stack": [
            Cors,
            ErrorPrinter,
            HTTPAuthenticator,
            GateMiddleware,
            WsgiDavDirBrowser,
            RequestResolver,
        ],
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
    return WsgiDAVApp(dav_config)


def start_webdav_server():
    """Start WebDAV server in a background thread."""
    app = create_webdav_app()
    server = CherootServer(("0.0.0.0", 8001), app)
    thread = threading.Thread(target=server.start, daemon=True)
    thread.start()
    return server


# Alias kept for clarity in callers.
create_app = create_webdav_app
