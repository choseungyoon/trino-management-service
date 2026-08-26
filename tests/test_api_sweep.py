"""Every JSON API route, walked once.

⛔ The point is route *shape*, not behaviour. The web layer shipped a 422
because a literal path segment was registered after a typed parameter and got
parsed as one; nothing caught it because the screen sweep only walks GETs and
nothing walked the API at all.

A missing row, a missing cluster, an id that does not exist - all of those are
fine answers here. A 500 is not, and neither is a route that cannot be reached
because another one shadows it.
"""

import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))

try:
    import httpx  # noqa: F401
    from fastapi import FastAPI  # noqa: F401
    import multipart  # noqa: F401

    WEB_DEPS = True
except ImportError:  # pragma: no cover - environment dependent
    WEB_DEPS = False

import test_web_restart  # noqa: E402
from test_web_routes import client_for, sign_in  # noqa: E402

#: Path parameters the sweep can fill. A route whose parameter is missing here
#: fails loudly rather than being skipped - an unwalked route is the one that
#: breaks.
PARAMS = {
    "cluster": "prod-a",
    "query_id": "20260808_000000_00001_abcde",
    "test_id": "H-01",
    "sequence_id": "1",
    "host": "w1",
    "row_id": "1",
    "selector_id": "1",
    "revision_id": "1",
    "run_id": "1",
    "key": "W-1",
    "set_key": "smoke",
    "name": "scan",
    "baseline_id": "1",
    "candidate_id": "2",
}


def _path_params(path):
    return re.findall(r"{(\w+)}", path)


@unittest.skipUnless(WEB_DEPS, "web dependencies are not installed")
class ApiSweepTest(unittest.IsolatedAsyncioTestCase):
    def app(self):
        # The fully-wired app from the screen sweep, so every feature is on.
        # Imported as a module, not a class: importing the class makes
        # pytest collect its tests a second time under this file's name.
        app, _config = test_web_restart.EveryScreenTest()._app()
        return app

    def _api_routes(self, app, method):
        found = []
        for route in app.routes:
            path = getattr(route, "path", "")
            if not path.startswith("/api/v1"):
                continue
            if method not in (getattr(route, "methods", set()) or set()):
                continue
            missing = [p for p in _path_params(path) if p not in PARAMS]
            self.assertEqual(
                [], missing,
                "{} has path parameter(s) {} with no sweep value - add them "
                "to PARAMS so the route is walked".format(path, missing))
            found.append(path)
        return found

    @staticmethod
    def _fill(path):
        for name, value in PARAMS.items():
            path = path.replace("{" + name + "}", value)
        return path

    async def test_every_api_get_answers_without_a_traceback(self):
        app = self.app()
        paths = self._api_routes(app, "GET")
        self.assertGreater(len(paths), 20, "route discovery found almost nothing")

        client = client_for(app)
        async with client:
            await sign_in(client)
            for path in paths:
                response = await client.get(self._fill(path))
                # 404 and 400 are fine - the sweep asks for ids that do not
                # exist. 503 is fine and deliberate: a feature switched off
                # says so by name. 500 means nobody handled it.
                self.assertNotEqual(
                    500, response.status_code,
                    "{} answered 500: {}".format(path, response.text[:200]))
                if response.status_code == 503:
                    self.assertTrue(
                        response.json()["error"]["message"],
                        "{} said 503 without saying what is off".format(path))

    async def test_no_api_route_is_shadowed_by_another(self):
        """Two routes resolving to one handler means one is unreachable.

        FastAPI matches in registration order, so a literal segment declared
        after a parameter of the same depth never runs.
        """
        app = self.app()
        seen = {}
        for route in app.routes:
            path = getattr(route, "path", "")
            if not path.startswith("/api/v1"):
                continue
            for method in getattr(route, "methods", set()) or set():
                key = (method, self._fill(path))
                self.assertNotIn(
                    key, seen,
                    "{} {} collides with {}".format(method, path, seen.get(key)))
                seen[key] = path

    async def test_every_api_route_refuses_an_anonymous_caller(self):
        """⛔ 401, never 500. A client that gets 500 cannot tell a broken
        server from an expired session, and cannot recover by signing in."""
        app = self.app()
        client = client_for(app)
        async with client:
            for path in self._api_routes(app, "GET"):
                response = await client.get(self._fill(path))
                self.assertEqual(401, response.status_code, path)


if __name__ == "__main__":
    unittest.main()
