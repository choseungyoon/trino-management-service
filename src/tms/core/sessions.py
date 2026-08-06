"""Signed session tokens (FR-PT-03).

Stateless by design: `tms-api` is meant to scale out, and a server-side session
table would make every replica need the same store on the request path. The
token carries its own expiry and is signed with a shared secret, so any replica
can validate it.

Two clocks, as the requirement asks for:

* absolute expiry - never extended. A session cannot live forever by being used.
* idle expiry - extended on each request by re-issuing the token.

Known limitation, accepted for the temporary local-auth mode (D-007): logout
cannot revoke a token server-side, so a stolen token stays valid until it
expires. Idle timeout is what bounds that. AD integration replaces this.

Python 3.9 compatible.
"""

import base64
import hmac
import json
import time
from hashlib import sha256
from typing import Any, Dict, List, Optional


class SessionError(Exception):
    pass


class SessionExpired(SessionError):
    pass


class SessionInvalid(SessionError):
    pass


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


class SessionCodec:
    def __init__(
        self,
        secret: str,
        idle_timeout_seconds: float = 1800.0,
        absolute_timeout_seconds: float = 43200.0,
    ) -> None:
        if not secret:
            # Refusing to start beats generating a random secret: an ephemeral
            # one silently breaks multi-replica login and every restart.
            raise SessionError(
                "session secret is required; set TMS_SESSION_SECRET or "
                "portal.session_secret in config.secret.yaml"
            )
        self._secret = secret.encode("utf-8")
        self.idle_timeout_seconds = idle_timeout_seconds
        self.absolute_timeout_seconds = absolute_timeout_seconds

    def _sign(self, payload: bytes) -> str:
        return _b64encode(hmac.new(self._secret, payload, sha256).digest())

    def issue(
        self,
        username: str,
        roles: List[str],
        now: Optional[float] = None,
        issued_at: Optional[float] = None,
        must_change_password: bool = False,
    ) -> str:
        """Create a token. `issued_at` is carried through on re-issue so the
        absolute deadline is anchored to the original login."""
        now = time.time() if now is None else now
        claims = {
            "u": username,
            "r": list(roles),
            "iat": issued_at if issued_at is not None else now,
            "seen": now,
            "chg": bool(must_change_password),
        }
        payload = json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return "{}.{}".format(_b64encode(payload), self._sign(payload))

    def verify(self, token: str, now: Optional[float] = None) -> Dict[str, Any]:
        now = time.time() if now is None else now
        if not token or "." not in token:
            raise SessionInvalid("malformed session token")
        encoded_payload, signature = token.rsplit(".", 1)
        try:
            payload = _b64decode(encoded_payload)
        except Exception as exc:  # noqa: BLE001
            raise SessionInvalid("malformed session token") from exc

        if not hmac.compare_digest(self._sign(payload), signature):
            raise SessionInvalid("session signature mismatch")

        try:
            claims = json.loads(payload.decode("utf-8"))
        except ValueError as exc:
            raise SessionInvalid("malformed session payload") from exc

        issued_at = float(claims.get("iat", 0))
        last_seen = float(claims.get("seen", 0))

        if now - issued_at > self.absolute_timeout_seconds:
            raise SessionExpired("session exceeded its absolute lifetime")
        if now - last_seen > self.idle_timeout_seconds:
            raise SessionExpired("session idle timeout")

        return {
            "username": claims.get("u"),
            "roles": list(claims.get("r") or []),
            "issued_at": issued_at,
            "last_seen": last_seen,
            "must_change_password": bool(claims.get("chg")),
        }

    def refresh(self, claims: Dict[str, Any], now: Optional[float] = None) -> str:
        """Slide the idle window without moving the absolute deadline."""
        return self.issue(
            username=claims["username"],
            roles=claims["roles"],
            now=now,
            issued_at=claims["issued_at"],
            must_change_password=claims.get("must_change_password", False),
        )
