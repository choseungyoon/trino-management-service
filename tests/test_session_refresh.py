"""Regression guard for the session-refresh clobber bug.

Found by running the app against a real PostgreSQL, not by unit tests: the
`slide_session` middleware rebuilt the session cookie from the *request's*
claims and wrote it after the handler had already issued a new one. A successful
password change was therefore undone on its own response, leaving the caller
permanently stuck behind the must_change_password gate.

FastAPI is not installed in every environment, so this reproduces the ordering
with the same two primitives the middleware uses - issue() in the handler,
refresh() in the middleware - and asserts the rule that fixes it: a response
that already carries a session cookie is never refreshed over.
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.core.sessions import SessionCodec  # noqa: E402

SECRET = "regression-secret"


class FakeResponse:
    """Stands in for a Starlette response's set-cookie header list."""

    def __init__(self):
        self.cookies = []

    def set_cookie(self, name, value):
        self.cookies.append("{}={}".format(name, value))

    def already_sets(self, name):
        return any(c.startswith(name + "=") for c in self.cookies)


COOKIE = "tms_session"


def middleware_refresh(codec, response, claims):
    """The corrected middleware rule."""
    if response.already_sets(COOKIE):
        return
    response.set_cookie(COOKIE, codec.refresh(claims))


class SessionRefreshTest(unittest.TestCase):
    def setUp(self):
        self.codec = SessionCodec(
            SECRET, idle_timeout_seconds=1800, absolute_timeout_seconds=43200
        )

    def test_password_change_response_is_not_clobbered(self):
        """The exact bug: the handler clears the flag, the middleware restores it."""
        stale_claims = self.codec.verify(
            self.codec.issue("syhcho", ["admin"], must_change_password=True)
        )
        self.assertTrue(stale_claims["must_change_password"])

        response = FakeResponse()
        # Handler issues a clean token after a successful password change.
        response.set_cookie(COOKIE, self.codec.issue("syhcho", ["admin"]))
        # Middleware runs afterwards with the request's stale claims.
        middleware_refresh(self.codec, response, stale_claims)

        self.assertEqual(len(response.cookies), 1, "middleware wrote a second cookie")
        final = response.cookies[0].split("=", 1)[1]
        self.assertFalse(
            self.codec.verify(final)["must_change_password"],
            "the middleware restored the temporary-password flag",
        )

    def test_ordinary_request_still_slides_the_idle_window(self):
        """The fix must not disable refresh on responses that set no cookie."""
        claims = self.codec.verify(self.codec.issue("syhcho", ["admin"]))
        response = FakeResponse()
        middleware_refresh(self.codec, response, claims)
        self.assertEqual(len(response.cookies), 1, "idle window was not extended")

    def test_refresh_preserves_the_temporary_flag_when_it_is_still_true(self):
        """A plain request must not accidentally clear the gate either."""
        claims = self.codec.verify(
            self.codec.issue("syhcho", ["admin"], must_change_password=True)
        )
        response = FakeResponse()
        middleware_refresh(self.codec, response, claims)
        refreshed = response.cookies[0].split("=", 1)[1]
        self.assertTrue(self.codec.verify(refreshed)["must_change_password"])

    def test_middleware_rule_is_present_in_the_source(self):
        """Keeps the guard from being deleted as a redundant-looking check."""
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(
            os.path.join(repo, "src", "tms", "api", "main.py"), encoding="utf-8"
        ) as handle:
            source = handle.read()
        self.assertIn("already_set", source)
        self.assertIn("set-cookie", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
