"""The live screens must actually auto-refresh.

base.html renders `data-refresh` only when the context carries
`refresh_seconds`, and tms.js starts its timer only when that attribute is
present. Wiring one without the other fails silently: every page looks correct,
renders correct data, and then sits frozen until the operator reloads by hand.

That is exactly what shipped (found 2026-08-07 in production use) - the
template hook and the JavaScript were both there, but no route ever put
`refresh_seconds` in the context, so `interval` parsed to 0 and the timer never
started. Nothing failed loudly enough to notice.

These tests assert the contract from both ends: the routes supply the value,
and the template turns it into the attribute the script looks for.
"""

import os
import re
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(REPO, "src", "tms", "web")

# Screens showing data that goes stale on its own.
LIVE_PAGES = ("overview", "queries", "health")
# Screens that must NOT reload under the reader.
STATIC_PAGES = ("audit", "account")


def read(*parts):
    with open(os.path.join(WEB, *parts), encoding="utf-8") as handle:
        return handle.read()


class RefreshContractTest(unittest.TestCase):
    """The three pieces must agree: JS reads the attribute, the template emits
    it, the routes supply the value."""

    def test_script_reads_the_attribute(self):
        js = read("static", "tms.js")
        self.assertIn("data-refresh", js)
        self.assertRegex(js, r"getAttribute\(\s*[\"']data-refresh[\"']\s*\)")

    def test_template_emits_the_attribute_from_refresh_seconds(self):
        html = read("templates", "base.html")
        self.assertRegex(html, r"data-refresh=\"\{\{\s*refresh_seconds\s*\}\}\"")
        self.assertIn("{% if refresh_seconds %}", html)

    def test_routes_actually_supply_refresh_seconds(self):
        """The step that was missing. Without it the other two are dead code."""
        routes = read("routes.py")
        self.assertIn(
            '"refresh_seconds"',
            routes,
            "routes.py never puts refresh_seconds in the template context, so "
            "data-refresh is never rendered and no page auto-refreshes.",
        )

    def test_live_pages_have_a_nonzero_interval(self):
        routes = read("routes.py")
        block = re.search(r"refresh_by_page\s*=\s*\{(.*?)\}", routes, re.S)
        self.assertIsNotNone(block, "no refresh_by_page mapping found in routes.py")
        mapping = dict(
            (name, int(value))
            for name, value in re.findall(
                r'"([a-z_]+)"\s*:\s*max\(int\([^)]*\)\s*,\s*(\d+)\)', block.group(1)
            )
        )
        for page in LIVE_PAGES:
            self.assertIn(page, mapping, "live page '{}' never refreshes".format(page))
            self.assertGreater(mapping[page], 0)

    def test_the_audit_log_does_not_reload_under_the_reader(self):
        """Deliberate: it is a record being read, not a dashboard."""
        routes = read("routes.py")
        block = re.search(r"refresh_by_page\s*=\s*\{(.*?)\}", routes, re.S)
        listed = re.findall(r'"([a-z_]+)"\s*:', block.group(1))
        for page in STATIC_PAGES:
            self.assertNotIn(page, listed)


class RenderedPageTest(unittest.TestCase):
    """End to end through Jinja: does the attribute reach the HTML?"""

    def setUp(self):
        try:
            from jinja2 import ChainableUndefined, Environment, FileSystemLoader
        except ImportError:  # pragma: no cover - jinja2 is a runtime dep
            self.skipTest("jinja2 not installed")
        from tms.web.formatting import FILTERS

        # Tolerant undefined: this test is about one attribute on <body>, not
        # about supplying every variable the full layout uses.
        self.env = Environment(
            loader=FileSystemLoader(os.path.join(WEB, "templates")),
            undefined=ChainableUndefined,
        )
        # Same registration the app does, or base.html will not compile.
        self.env.filters.update(FILTERS)
        # Starlette injects url_for at render time; stub it so this test can
        # render base.html without standing up the whole application.
        self.env.globals["url_for"] = lambda name, **kw: "/" + str(name)

    def _body_tag(self, **context):
        source = read("templates", "base.html")
        rendered = self.env.from_string(source).render(**context)
        match = re.search(r"<body[^>]*>", rendered)
        self.assertIsNotNone(match, "no <body> tag rendered")
        return match.group(0)

    def test_attribute_present_when_seconds_supplied(self):
        self.assertIn('data-refresh="5"', self._body_tag(refresh_seconds=5))

    def test_attribute_absent_when_zero(self):
        """0 must not render data-refresh="0" - the script would parse it and
        schedule an immediate reload loop."""
        self.assertNotIn("data-refresh", self._body_tag(refresh_seconds=0))

    def test_attribute_absent_when_missing(self):
        self.assertNotIn("data-refresh", self._body_tag())


if __name__ == "__main__":
    unittest.main()
