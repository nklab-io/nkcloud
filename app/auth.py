import base64
import hashlib
import hmac
import secrets


def _pad_base64(value: str) -> str:
    return value + "=" * (-len(value) % 4)


def hash_password(password: str) -> str:
    """Create a PBKDF2-SHA256 hash string."""
    salt = secrets.token_urlsafe(16)
    iterations = 260000
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations)
    hash_b64 = base64.urlsafe_b64encode(derived).decode().rstrip("=")
    return f"pbkdf2_sha256:{iterations}:{salt}:{hash_b64}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify password against a stored PBKDF2 hash string."""
    try:
        parts = stored_hash.split(chr(58), 3) if chr(58) in stored_hash else stored_hash.split(chr(36), 3)
        algorithm, iterations, salt, expected = parts
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations))
    expected_bytes = base64.urlsafe_b64decode(_pad_base64(expected))
    return hmac.compare_digest(derived, expected_bytes)
