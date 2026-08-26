"""Serving the built React console.

⛔ The two properties that matter are both about what happens when something
is missing: a checkout without a build must still start, and a deep link into
the app must not 404 just because the server does not know the client's routes.
"""

import os
import pathlib
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))
sys.path.insert(0, _HERE)

try:
    import httpx  # noqa: F401
    from fastapi import FastAPI  # noqa: F401

    WEB_DEPS = True
except ImportError:  # pragma: no cover - environment dependent
    WEB_DEPS = False

from tms.api.main import create_app  # noqa: E402
from tms.ui import mount as ui  # noqa: E402

from console import build_service, client_for, sign_in  # noqa: E402


@unittest.skipUnless(WEB_DEPS, "fastapi/httpx not installed")
class ConsoleMountTest(unittest.IsolatedAsyncioTestCase):
    def app(self):
        config, service, _trino = build_service()
        return create_app(config=config, service=service)

    async def test_a_deep_link_returns_the_document_not_a_404(self):
        """Routing happens in the browser. The server cannot know which paths
        belong to the client router, so every one of them gets the app."""
        if not ui.available():
            self.skipTest("the console is not built in this checkout")
        client = client_for(self.app())
        async with client:
            for path in ("/", "/login", "/benchmark/sets/nightly"):
                response = await client.get(path)
                self.assertEqual(200, response.status_code, path)
                self.assertIn('id="root"', response.text, path)

    async def test_the_document_is_never_cached(self):
        """index.html names the current bundle hashes. A cached copy points at
        a bundle that no longer exists."""
        if not ui.available():
            self.skipTest("the console is not built in this checkout")
        client = client_for(self.app())
        async with client:
            response = await client.get("/")
        self.assertIn("no-store", response.headers.get("cache-control", ""))

    async def test_the_console_never_shadows_the_api(self):
        """⛔ The catch-all owns / now, so this is the property that keeps the
        API usable: an /api/ path reaching the SPA would answer HTML to a
        client expecting JSON, turning "wrong endpoint" into a parse error."""
        client = client_for(self.app())
        async with client:
            unauthenticated = await client.get("/api/v1/me")
            self.assertEqual(401, unauthenticated.status_code)
            self.assertIn("application/json",
                          unauthenticated.headers["content-type"])

            await sign_in(client)
            missing = await client.get("/api/v1/no-such-thing")
            self.assertEqual(404, missing.status_code)
            self.assertIn("application/json", missing.headers["content-type"])

    async def test_the_session_ceremony_survived_the_cutover(self):
        """Sign-in, cookie flags, sign-out. The console has no other way in.

        `Secure` matters: the whole HTTPS deployment requirement rests on it.
        """
        client = client_for(self.app())
        async with client:
            refused = await sign_in(client, password="wrong")
            self.assertEqual(401, refused.status_code)
            self.assertNotIn("tms_session", client.cookies)

            accepted = await sign_in(client)
            self.assertEqual(200, accepted.status_code)
            cookie = accepted.headers.get("set-cookie", "")
            self.assertIn("tms_session", cookie)
            self.assertIn("Secure", cookie)
            self.assertIn("HttpOnly", cookie)

            self.assertEqual(200, (await client.get("/api/v1/me")).status_code)
            await client.post("/api/v1/logout")
            self.assertEqual(401, (await client.get("/api/v1/me")).status_code)

    async def test_no_server_route_shadows_a_screen(self):
        """⛔ The console owns / and is mounted last, so any route registered
        before it wins - silently. `/health` is the liveness probe, and the
        health screen sat behind it returning `{"status":"ok"}` to a browser
        until this test was written.

        The screen list is read out of the router, not typed here: a new screen
        that collides has to be caught by the check that already exists.
        """
        app = self.app()
        server_paths = {
            getattr(route, "path", "") for route in app.routes
            if "GET" in (getattr(route, "methods", set()) or set())
            and "spa_path" not in getattr(route, "path", "")
        }
        server_paths.discard("/")

        router = pathlib.Path(
            _HERE, "..", "frontend", "src", "main.tsx").resolve()
        screens = {"/" + m for m in re.findall(r'<Route path="([a-z][a-z0-9/-]*)"',
                                               router.read_text(encoding="utf-8"))}
        self.assertTrue(screens, "no screens found in main.tsx")

        # Guard the guard: /health is a real server route, so a screen there
        # must be reported.
        self.assertIn("/health", server_paths)
        self.assertEqual(["/health"], sorted({"/health"} & server_paths))

        collisions = sorted(screens & server_paths)
        self.assertEqual(
            [], collisions,
            "these console screens are shadowed by a server route and will "
            "never render: {}".format(collisions))

    def test_a_checkout_without_a_build_still_starts(self):
        """Working on the backend must not require a Node toolchain."""
        real = ui.ASSETS
        try:
            ui.ASSETS = os.path.join(real, "does-not-exist")
            self.assertFalse(ui.available())
            self.assertIsNone(ui.mount(self.app()))
        finally:
            ui.ASSETS = real


if __name__ == "__main__":
    unittest.main()
