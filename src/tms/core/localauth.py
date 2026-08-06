"""Local account authentication - temporary, until AD integration (D-007).

Named accounts rather than one shared `admin`. The cost is identical and the
difference matters: with a shared login every audit record says `admin`, and
"who killed this query?" - the question FR-AUDIT-ACTION exists to answer -
becomes unanswerable. A temporary auth mechanism should not quietly disable the
release's main feature.

Accounts live in config.secret.yaml (gitignored) or the environment, never in
config.yaml: this repository is PUBLIC (D-002). Only PBKDF2 hashes are stored.

Python 3.9 compatible.
"""

import logging
import time
from typing import Dict, List, Optional

from tms.core.passwords import PasswordError, check_password_strength, hash_password, verify_password

log = logging.getLogger(__name__)

# Deliberately coarse. Enough to make online guessing impractical without
# needing shared state between replicas for a temporary mechanism.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 300


class AuthError(Exception):
    pass


class InvalidCredentials(AuthError):
    """Deliberately identical for unknown user and wrong password: telling them
    apart hands an attacker a user enumeration oracle."""


class AccountLocked(AuthError):
    pass


class PasswordChangeRequired(AuthError):
    pass


class LocalUser:
    __slots__ = ("username", "password_hash", "roles", "must_change_password")

    def __init__(
        self,
        username: str,
        password_hash: str,
        roles: List[str],
        must_change_password: bool = False,
    ) -> None:
        self.username = username
        self.password_hash = password_hash
        self.roles = list(roles)
        self.must_change_password = must_change_password

    def __repr__(self) -> str:
        # Never render the hash: config objects reach tracebacks and debug logs.
        return "LocalUser({}, roles={})".format(self.username, self.roles)


class LocalAuthenticator:
    def __init__(self, users: Dict[str, LocalUser], clock=None) -> None:
        self.users = users
        self._clock = clock or time.monotonic
        self._failures: Dict[str, List[float]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.users)

    def _record_failure(self, username: str) -> None:
        now = self._clock()
        attempts = [t for t in self._failures.get(username, []) if now - t < LOCKOUT_SECONDS]
        attempts.append(now)
        self._failures[username] = attempts

    def _is_locked(self, username: str) -> bool:
        now = self._clock()
        attempts = [t for t in self._failures.get(username, []) if now - t < LOCKOUT_SECONDS]
        self._failures[username] = attempts
        return len(attempts) >= MAX_FAILED_ATTEMPTS

    def seconds_until_unlock(self, username: str) -> float:
        attempts = self._failures.get(username, [])
        if not attempts:
            return 0.0
        return max(0.0, LOCKOUT_SECONDS - (self._clock() - attempts[0]))

    def authenticate(self, username: str, password: str) -> LocalUser:
        if self._is_locked(username):
            raise AccountLocked(
                "계정이 잠겼다. {:.0f}초 후 다시 시도하라".format(
                    self.seconds_until_unlock(username)
                )
            )

        user = self.users.get(username)
        if user is None:
            # Spend comparable time on an unknown user so response timing does
            # not reveal which accounts exist.
            verify_password(password, hash_password("dummy-timing-equaliser"))
            self._record_failure(username)
            raise InvalidCredentials("사용자명 또는 비밀번호가 올바르지 않다")

        if not verify_password(password, user.password_hash):
            self._record_failure(username)
            log.warning("failed login for %s", username)
            raise InvalidCredentials("사용자명 또는 비밀번호가 올바르지 않다")

        self._failures.pop(username, None)
        return user

    def change_password(self, username: str, current_password: str, new_password: str) -> str:
        """Verify the current password and return a hash for the new one.

        Returns the hash rather than persisting it: accounts live in a
        gitignored config file that the process does not own. The caller writes
        it, and the runbook documents how.
        """
        user = self.authenticate(username, current_password)
        if current_password == new_password:
            raise PasswordError("새 비밀번호가 기존과 동일하다")
        check_password_strength(new_password)
        new_hash = hash_password(new_password)
        user.password_hash = new_hash
        user.must_change_password = False
        return new_hash


def build_users(raw: Optional[Dict[str, Dict]]) -> Dict[str, LocalUser]:
    """Build the account table from configuration.

    A plaintext `password` key is rejected outright rather than hashed on the
    fly: accepting it would let a plaintext secret sit in a file someone later
    commits to a PUBLIC repository.
    """
    users: Dict[str, LocalUser] = {}
    for username, entry in (raw or {}).items():
        if not isinstance(entry, dict):
            raise AuthError("local_users.{}: expected a mapping".format(username))
        if "password" in entry:
            raise AuthError(
                "local_users.{}: plaintext 'password' is not accepted. Use "
                "scripts/hash_password.py and store 'password_hash'.".format(username)
            )
        password_hash = entry.get("password_hash")
        if not password_hash:
            raise AuthError("local_users.{}: password_hash is required".format(username))
        roles = entry.get("roles") or []
        if not roles:
            raise AuthError("local_users.{}: at least one role is required".format(username))
        users[username] = LocalUser(
            username=username,
            password_hash=str(password_hash),
            roles=list(roles),
            must_change_password=bool(entry.get("must_change_password", False)),
        )
    return users
