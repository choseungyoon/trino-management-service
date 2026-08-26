"""Serving the built React console.

⛔ The two properties that matter are both about what happens when something
is missing: a checkout without a build must still start, and a deep link into
the app must not 404 just because the server does not know the client's routes.
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))
sys.path.insert(0, _HERE)

try:
    import httpx  # noqa: F401
    from fastapi import FastAPI  # noqa: F401
    import multipart  # noqa: F401

    WEB_DEPS = True
except ImportError:  # pragma: no cover - environment dependent
    WEB_DEPS = False

from tms.api.main import create_app  # noqa: E402
from tms.ui import mount as ui  # noqa: E402

from test_web_routes import build_service, client_for  # noqa: E402


@unittest.skipUnless(WEB_DEPS, "web dependencies are not installed")
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
            for path in ("/app", "/app/", "/app/benchmark/sets/nightly"):
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
            response = await client.get("/app")
        self.assertIn("no-store", response.headers.get("cache-control", ""))

    async def test_the_console_never_shadows_the_api(self):
        """⛔ Its catch-all is scoped to /app. An /api/ path reaching the SPA
        would answer HTML to a client expecting JSON."""
        client = client_for(self.app())
        async with client:
            response = await client.get("/api/v1/me")
        self.assertEqual(401, response.status_code)
        self.assertIn("application/json", response.headers["content-type"])

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
