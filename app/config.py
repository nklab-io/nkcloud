import os

FILE_ROOT = os.environ.get("NKCLOUD_FILE_ROOT", "/data")
THUMB_DIR = os.environ.get("NKCLOUD_THUMB_DIR", "/app/data/thumbs")
DB_PATH = os.environ.get("NKCLOUD_DB_PATH", "/app/data/nkcloud.db")

# SESSION_SECRET: loaded from env, or auto-generated and persisted to file
SESSION_SECRET = os.environ.get("NKCLOUD_SESSION_SECRET", "")
# Legacy: PASSWORD_HASH is no longer required (users table replaces it)
PASSWORD_HASH = os.environ.get("NKCLOUD_PASSWORD_HASH", "")

SESSION_COOKIE = "nkcloud_session"
CSRF_COOKIE = "nkcloud_csrf"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days
LOGIN_WINDOW_SECONDS = 15 * 60
LOCKOUT_SECONDS = 15 * 60
MAX_FAILED_ATTEMPTS = 5

CHUNK_DIR = os.environ.get("NKCLOUD_CHUNK_DIR", "/app/data/chunks")
CHUNK_SIZE = 5 * 1024 * 1024  # 5MB
MAX_SEARCH_RESULTS = 200
THUMB_SIZE = (300, 300)
PREVIEW_SIZE = (1920, 1920)

HOMES_DIR = "_homes"
TRASH_DIR = ".trash"
TRASH_RETENTION_DAYS = 14
DATA_DIR = os.environ.get("NKCLOUD_DATA_DIR", "/app/data")
SESSION_SECRET_FILE = os.path.join(DATA_DIR, ".session_secret")
DEFAULT_QUOTA_BYTES = 0  # 0 = unlimited

# Text preview
TEXT_PREVIEW_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
TEXT_PREVIEW_EXTS = {
    ".txt", ".md", ".markdown", ".log", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".env", ".properties",
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".html", ".htm", ".css", ".scss",
    ".sass", ".less", ".vue", ".svelte",
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd",
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hxx",
    ".java", ".kt", ".swift", ".go", ".rs", ".rb", ".php", ".pl", ".lua",
    ".sql", ".graphql", ".proto",
    ".r", ".jl", ".scala", ".clj", ".hs", ".ml", ".ex", ".exs",
    ".dockerfile", ".gitignore", ".gitattributes", ".editorconfig",
    ".svg", ".diff", ".patch",
}
