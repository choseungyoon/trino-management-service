"""Tests for local account authentication and sessions (D-007, temporary).

The properties that matter for a mechanism guarding an account that can kill
production queries:

* No plaintext password is ever accepted in configuration. This repository is
  PUBLIC, so a config file that tolerates plaintext is one careless commit away
  from disclosure.
* Unknown user and wrong password are indistinguishable, in message and in
  rough timing. Otherwise the login route is a user-enumeration oracle.
* A temporary password cannot be used to do anything except change itself.
* The absolute session deadline survives refresh; only the idle window slides.
"""

import os
import sys
import time
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.core.localauth import (  # noqa: E402
    MAX_FAILED_ATTEMPTS,
    AccountLocked,
    AuthError,
    InvalidCredentials,
    LocalAuthenticator,
    build_users,
)
from tms.core.passwords import (  # noqa: E402
    PasswordError,
    check_password_strength,
    hash_password,
    verify_password,
)
from tms.core.sessions import (  # noqa: E402
    SessionCodec,
    SessionError,
    SessionExpired,
    SessionInvalid,
)

GOOD_PASSWORD = "Correct-Horse-9"


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_authenticator(must_change=False, clock=None):
    users = build_users(
        {
            "syhcho": {
                "password_hash": hash_password(GOOD_PASSWORD, iterations=1000),
                "roles": ["admin"],
                "must_change_password": must_change,
            }
        }
    )
    return LocalAuthenticator(users, clock=clock)


class PasswordHashTest(unittest.TestCase):
    def test_roundtrip(self):
        encoded = hash_password(GOOD_PASSWORD, iterations=1000)
        self.assertTrue(verify_password(GOOD_PASSWORD, encoded))
        self.assertFalse(verify_password("wrong", encoded))

    def test_hash_is_salted(self):
        a = hash_password(GOOD_PASSWORD, iterations=1000)
        b = hash_password(GOOD_PASSWORD, iterations=1000)
        self.assertNotEqual(a, b, "identical hashes mean the salt is not random")

    def test_plaintext_never_appears_in_the_hash(self):
        self.assertNotIn(GOOD_PASSWORD, hash_password(GOOD_PASSWORD, iterations=1000))

    def test_malformed_hash_denies_rather_than_raises(self):
        for encoded in ("", "garbage", "bcrypt$1$x$y", "pbkdf2_sha256$notanint$a$b"):
            with self.subTest(encoded=encoded):
                self.assertFalse(verify_password(GOOD_PASSWORD, encoded))

    def test_strength_rules(self):
        for weak in ("short", "alllowercaseonly", "1234567890123"):
            with self.subTest(weak=weak):
                with self.assertRaises(PasswordError):
                    check_password_strength(weak)
        check_password_strength(GOOD_PASSWORD)


class BuildUsersTest(unittest.TestCase):
    def test_plaintext_password_key_is_rejected(self):
        """Accepting it would put a plaintext secret in a file destined for git."""
        with self.assertRaises(AuthError) as ctx:
            build_users({"admin": {"password": "hunter2", "roles": ["admin"]}})
        self.assertIn("hash_password", str(ctx.exception))

    def test_missing_hash_is_rejected(self):
        with self.assertRaises(AuthError):
            build_users({"admin": {"roles": ["admin"]}})

    def test_missing_roles_is_rejected(self):
        with self.assertRaises(AuthError):
            build_users({"admin": {"password_hash": "x"}})

    def test_repr_does_not_leak_the_hash(self):
        users = build_users(
            {"admin": {"password_hash": "pbkdf2_sha256$1$a$b", "roles": ["admin"]}}
        )
        self.assertNotIn("pbkdf2", repr(users["admin"]))


class AuthenticateTest(unittest.TestCase):
    def test_valid_login(self):
        user = make_authenticator().authenticate("syhcho", GOOD_PASSWORD)
        self.assertEqual(user.username, "syhcho")
        self.assertEqual(user.roles, ["admin"])

    def test_wrong_password_is_rejected(self):
        with self.assertRaises(InvalidCredentials):
            make_authenticator().authenticate("syhcho", "nope")

    def test_unknown_user_and_wrong_password_are_indistinguishable(self):
        """Different messages would turn login into a user-enumeration oracle."""
        auth = make_authenticator()
        try:
            auth.authenticate("nobody", GOOD_PASSWORD)
        except InvalidCredentials as exc:
            unknown_user_message = str(exc)
        try:
            auth.authenticate("syhcho", "wrong")
        except InvalidCredentials as exc:
            wrong_password_message = str(exc)
        self.assertEqual(unknown_user_message, wrong_password_message)

    def test_unknown_user_still_spends_time_hashing(self):
        """A fast reject on unknown users leaks which accounts exist."""
        auth = make_authenticator()
        started = time.time()
        with self.assertRaises(InvalidCredentials):
            auth.authenticate("nobody", GOOD_PASSWORD)
        self.assertGreater(time.time() - started, 0.0005)

    def test_lockout_after_repeated_failures(self):
        clock = FakeClock()
        auth = make_authenticator(clock=clock)
        for _ in range(MAX_FAILED_ATTEMPTS):
            with self.assertRaises(InvalidCredentials):
                auth.authenticate("syhcho", "wrong")
        with self.assertRaises(AccountLocked):
            auth.authenticate("syhcho", GOOD_PASSWORD)

    def test_lockout_expires(self):
        clock = FakeClock()
        auth = make_authenticator(clock=clock)
        for _ in range(MAX_FAILED_ATTEMPTS):
            with self.assertRaises(InvalidCredentials):
                auth.authenticate("syhcho", "wrong")
        clock.advance(301)
        self.assertEqual(auth.authenticate("syhcho", GOOD_PASSWORD).username, "syhcho")

    def test_successful_login_clears_the_failure_count(self):
        clock = FakeClock()
        auth = make_authenticator(clock=clock)
        for _ in range(MAX_FAILED_ATTEMPTS - 1):
            with self.assertRaises(InvalidCredentials):
                auth.authenticate("syhcho", "wrong")
        auth.authenticate("syhcho", GOOD_PASSWORD)
        with self.assertRaises(InvalidCredentials):
            auth.authenticate("syhcho", "wrong")  # must not lock immediately


class ChangePasswordTest(unittest.TestCase):
    def test_change_requires_the_current_password(self):
        with self.assertRaises(InvalidCredentials):
            make_authenticator().change_password("syhcho", "wrong", "Brand-New-Pass-1")

    def test_reusing_the_same_password_is_rejected(self):
        with self.assertRaises(PasswordError):
            make_authenticator().change_password("syhcho", GOOD_PASSWORD, GOOD_PASSWORD)

    def test_weak_new_password_is_rejected(self):
        with self.assertRaises(PasswordError):
            make_authenticator().change_password("syhcho", GOOD_PASSWORD, "weak")

    def test_change_clears_the_temporary_flag(self):
        auth = make_authenticator(must_change=True)
        self.assertTrue(auth.users["syhcho"].must_change_password)
        auth.change_password("syhcho", GOOD_PASSWORD, "Brand-New-Pass-1")
        self.assertFalse(auth.users["syhcho"].must_change_password)
        self.assertTrue(auth.authenticate("syhcho", "Brand-New-Pass-1"))


class SessionTest(unittest.TestCase):
    def _codec(self, idle=60, absolute=300):
        return SessionCodec("shared-secret", idle_timeout_seconds=idle, absolute_timeout_seconds=absolute)

    def test_empty_secret_is_refused(self):
        """An ephemeral secret breaks login on restart and across replicas."""
        with self.assertRaises(SessionError):
            SessionCodec("")

    def test_roundtrip(self):
        codec = self._codec()
        claims = codec.verify(codec.issue("syhcho", ["admin"], now=1000), now=1010)
        self.assertEqual(claims["username"], "syhcho")
        self.assertEqual(claims["roles"], ["admin"])

    def test_tampered_token_is_rejected(self):
        codec = self._codec()
        token = codec.issue("syhcho", ["viewer"], now=1000)
        payload, signature = token.rsplit(".", 1)
        forged = codec.issue("syhcho", ["admin"], now=1000).rsplit(".", 1)[0]
        with self.assertRaises(SessionInvalid):
            codec.verify("{}.{}".format(forged, signature), now=1010)

    def test_token_from_another_secret_is_rejected(self):
        other = SessionCodec("different-secret")
        with self.assertRaises(SessionInvalid):
            self._codec().verify(other.issue("syhcho", ["admin"], now=1000), now=1010)

    def test_idle_timeout(self):
        codec = self._codec(idle=60)
        token = codec.issue("syhcho", ["admin"], now=1000)
        codec.verify(token, now=1059)
        with self.assertRaises(SessionExpired):
            codec.verify(token, now=1061)

    def test_absolute_timeout_survives_refresh(self):
        """A session must not live forever by being used."""
        codec = self._codec(idle=60, absolute=300)
        token = codec.issue("syhcho", ["admin"], now=1000)
        now = 1000.0
        for _ in range(10):
            now += 30
            claims = codec.verify(token, now=now)
            token = codec.refresh(claims, now=now)
            if now - 1000 > 300:
                break
        with self.assertRaises(SessionExpired):
            codec.verify(token, now=1400)

    def test_refresh_slides_the_idle_window(self):
        codec = self._codec(idle=60, absolute=3600)
        token = codec.issue("syhcho", ["admin"], now=1000)
        token = codec.refresh(codec.verify(token, now=1050), now=1050)
        self.assertEqual(codec.verify(token, now=1100)["username"], "syhcho")

    def test_temporary_password_flag_is_carried_in_the_session(self):
        codec = self._codec()
        token = codec.issue("syhcho", ["admin"], now=1000, must_change_password=True)
        self.assertTrue(codec.verify(token, now=1010)["must_change_password"])

    def test_malformed_tokens_are_rejected(self):
        codec = self._codec()
        for token in ("", "no-dot", "a.b", "!!!.###"):
            with self.subTest(token=token):
                with self.assertRaises(SessionError):
                    codec.verify(token, now=1000)


class ShippedConfigTest(unittest.TestCase):
    def test_config_yaml_contains_no_local_accounts(self):
        """Accounts belong in the gitignored secret file - the repo is PUBLIC."""
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(repo, "config", "config.yaml"), encoding="utf-8") as handle:
            body = handle.read()
        self.assertNotIn("password_hash", body)
        self.assertNotIn("local_users:", body)
        self.assertNotIn("session_secret:", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
