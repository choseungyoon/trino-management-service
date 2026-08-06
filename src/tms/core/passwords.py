"""Password hashing for local accounts.

PBKDF2-HMAC-SHA256 from the standard library. bcrypt or argon2 would be
marginally better, but both are third-party wheels and this site reaches PyPI
through an Artifactory proxy; PBKDF2 with a high iteration count is the right
trade here and needs nothing installed.

Plaintext passwords never appear in configuration. `scripts/hash_password.py`
produces the hash on the operator's machine, and only the hash is stored.

Format: pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>

Python 3.9 compatible.
"""

import base64
import hashlib
import hmac
import secrets
from typing import Tuple

ALGORITHM = "pbkdf2_sha256"
# OWASP guidance for PBKDF2-HMAC-SHA256. Raise it, never lower it: existing
# hashes carry their own iteration count, so old ones keep verifying.
DEFAULT_ITERATIONS = 600_000
SALT_BYTES = 16
MIN_PASSWORD_LENGTH = 12


class PasswordError(Exception):
    pass


def hash_password(password: str, iterations: int = DEFAULT_ITERATIONS) -> str:
    if not password:
        raise PasswordError("password must not be empty")
    salt = secrets.token_bytes(SALT_BYTES)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "{}${}${}${}".format(
        ALGORITHM,
        iterations,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(derived).decode("ascii"),
    )


def _parse(encoded: str) -> Tuple[int, bytes, bytes]:
    try:
        algorithm, iterations, salt_b64, hash_b64 = encoded.split("$")
    except (ValueError, AttributeError) as exc:
        raise PasswordError("malformed password hash") from exc
    if algorithm != ALGORITHM:
        raise PasswordError("unsupported password hash algorithm: {}".format(algorithm))
    try:
        return int(iterations), base64.b64decode(salt_b64), base64.b64decode(hash_b64)
    except (ValueError, TypeError) as exc:
        raise PasswordError("malformed password hash") from exc


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time comparison. Returns False rather than raising on a
    malformed stored hash, so a corrupt entry denies access instead of
    crashing the login route."""
    if not password or not encoded:
        return False
    try:
        iterations, salt, expected = _parse(encoded)
    except PasswordError:
        return False
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(derived, expected)


def check_password_strength(password: str) -> None:
    """Minimum bar for an account that can kill production queries.

    Deliberately simple: length plus a character-class requirement. A dictionary
    check would need a wordlist this deployment cannot fetch.
    """
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise PasswordError(
            "Password must be at least {} characters.".format(MIN_PASSWORD_LENGTH)
        )
    classes = 0
    for predicate in (str.islower, str.isupper, str.isdigit):
        if any(predicate(c) for c in password):
            classes += 1
    if any(not c.isalnum() for c in password):
        classes += 1
    if classes < 3:
        raise PasswordError(
            "Password must contain at least 3 of: lowercase, uppercase, digit, symbol."
        )
