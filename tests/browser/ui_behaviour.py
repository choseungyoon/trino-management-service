"""Browser tests for behaviour that only exists in the browser.

`tms.js` has no Python coverage and never will - it is JavaScript. Everything
it does is progressive enhancement, which makes it exactly the kind of code
that can be silently dead without anything failing. That already happened once:
the auto-refresh timer never started because no route supplied
`refresh_seconds`, and the whole Python suite stayed green.

So these tests target the JS contract specifically:

  * auto-refresh actually reloads, and holds off while someone is typing
  * the destructive submit is one-shot (a double click must not send two kills)
  * a blank reason is refused in the browser before the round trip
  * the detail drawer opens as a <dialog> without losing the page behind it
  * the freshness ticker keeps counting between reloads
  * the theme toggle persists

Not part of the default suite: needs Playwright and a Chromium download, which
a locked-down corporate network may not allow. Run it locally.

    <venv>/bin/python -m unittest tests.browser.ui_behaviour -v

The filename deliberately does not start with `test_`, so `unittest discover`
skips it - same convention as tests/integration/. Browser tests take ~25s and
need a Chromium download; they must not slow the default suite.

Python 3.9 compatible.
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)

try:
    from playwright.sync_api import sync_playwright

    HAVE_PLAYWRIGHT = True
except ImportError:  # pragma: no cover - optional tool
    HAVE_PLAYWRIGHT = False

from tests.browser.harness import serve, sign_in  # noqa: E402


@unittest.skipUnless(HAVE_PLAYWRIGHT, "playwright not installed")
class UiBehaviourTest(unittest.TestCase):
    """One server and one browser for the whole class - both are slow to start."""

    @classmethod
    def setUpClass(cls):
        cls._server = serve()
        cls.base, cls.trino = cls._server.__enter__()
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()
        cls._server.__exit__(None, None, None)

    def setUp(self):
        # ignore_https_errors: the harness certificate is self-signed.
        self.context = self.browser.new_context(ignore_https_errors=True)
        self.page = self.context.new_page()

    def tearDown(self):
        self.context.close()

    # ------------------------------------------------------------------ login

    def test_sign_in_works_over_https(self):
        """If the Secure cookie were not stored, this loops forever."""
        sign_in(self.page, self.base)
        self.assertNotIn("/login", self.page.url)
        self.assertTrue(self.page.locator("text=Overview").first.is_visible())

    # ----------------------------------------------------------- auto-refresh

    def test_live_pages_carry_the_refresh_attribute(self):
        sign_in(self.page, self.base)
        for path, expected in (("/", "5"), ("/queries", "5"),
                               ("/clusters/prod-a/health", "15")):
            self.page.goto(self.base + path)
            self.assertEqual(expected,
                             self.page.get_attribute("body", "data-refresh"), path)

    def test_audit_log_does_not_auto_refresh(self):
        """Deliberate: reloading under a reader loses their place."""
        sign_in(self.page, self.base)
        self.page.goto(self.base + "/audit")
        self.assertIsNone(self.page.get_attribute("body", "data-refresh"))

    def test_the_page_actually_reloads(self):
        """The bug that shipped: attribute and script both fine, no reload."""
        sign_in(self.page, self.base)
        self.page.goto(self.base + "/queries")
        self.page.evaluate("window.__stillHere = true")
        # data-refresh is 5s; give it room on a loaded machine.
        self.page.wait_for_function("window.__stillHere === undefined", timeout=15000)

    def test_refresh_holds_off_while_typing(self):
        """Reloading mid-sentence during an incident is worse than stale data."""
        sign_in(self.page, self.base)
        self.page.goto(self.base + "/queries")
        self.page.focus('input[name="user"]')
        self.page.evaluate("window.__stillHere = true")
        self.page.wait_for_timeout(9000)  # well past two refresh intervals
        self.assertTrue(self.page.evaluate("window.__stillHere === true"),
                        "the page reloaded while an input had focus")

    # ------------------------------------------------------- write ceremonies

    def test_blank_reason_is_refused_without_a_round_trip(self):
        """The reason must not leave the browser empty.

        Asserting on validationMessage would prove nothing - the textarea is
        `required`, so the browser fills that in whether or not our guard runs.
        The real contract is that no request is sent and the page does not move.
        """
        sign_in(self.page, self.base)
        url = self.base + "/clusters/prod-a/queries/20260808_000000_00001_aaaaa/kill"
        self.page.goto(url)
        before = len(self.trino.killed)
        posted = []
        self.page.on("request", lambda r: posted.append(r.url)
                     if r.method == "POST" else None)
        self.page.locator(".btn--danger").click()
        self.page.wait_for_timeout(700)
        self.assertEqual([], posted, "a blank reason was sent to the server")
        self.assertEqual(before, len(self.trino.killed))
        self.assertEqual(url, self.page.url, "the page navigated away")

    def test_destructive_submit_is_one_shot(self):
        """A double click must not deliver two kills."""
        sign_in(self.page, self.base)
        self.page.goto(self.base
                       + "/clusters/prod-a/queries/20260808_000000_00002_bbbbb/kill")
        self.page.fill('[name="reason"]', "runaway query, owner paged")
        button = self.page.locator(".btn--danger")
        button.click()
        # The guard disables on the next tick; a fast second click must be a no-op.
        try:
            button.click(timeout=1500)
        except Exception:
            pass  # already disabled, which is the point
        self.page.wait_for_timeout(1500)
        self.assertEqual(1, len(self.trino.killed),
                         "the query was killed {} times".format(len(self.trino.killed)))

    # ---------------------------------------------------------------- drawer

    def test_query_row_opens_a_dialog_without_leaving_the_list(self):
        sign_in(self.page, self.base)
        self.page.goto(self.base + "/clusters/prod-a/queries")
        url_before = self.page.url
        self.page.click("[data-drawer]")
        self.page.wait_for_selector("#drawer[open]", timeout=5000)
        self.assertEqual(url_before, self.page.url, "navigated instead of opening")

    def test_escape_closes_the_drawer(self):
        sign_in(self.page, self.base)
        self.page.goto(self.base + "/clusters/prod-a/queries")
        self.page.click("[data-drawer]")
        self.page.wait_for_selector("#drawer[open]", timeout=5000)
        self.page.keyboard.press("Escape")
        self.page.wait_for_function(
            "!document.getElementById('drawer').open", timeout=5000)

    # --------------------------------------------------------------- ticker

    def test_freshness_label_keeps_counting(self):
        sign_in(self.page, self.base)
        self.page.goto(self.base + "/clusters/prod-a/health")  # 15s refresh, room to watch
        label = self.page.locator(".freshness [data-collected-at]")
        if label.count() == 0:
            self.skipTest("no freshness label on this page")
        first = label.inner_text()
        self.page.wait_for_timeout(3000)
        self.assertNotEqual(first, label.inner_text(),
                            "the ticker froze; the age shown would go stale silently")

    # ---------------------------------------------------------------- theme

    def test_theme_toggle_persists_across_navigation(self):
        sign_in(self.page, self.base)
        before = self.page.get_attribute("html", "data-theme")
        self.page.click('form[action="/ui/theme"] button')
        self.page.wait_for_load_state()
        after = self.page.get_attribute("html", "data-theme")
        self.assertNotEqual(before, after)
        self.page.goto(self.base + "/queries")
        self.assertEqual(after, self.page.get_attribute("html", "data-theme"))

    # ----------------------------------------------------------- no JS errors

    def test_no_console_errors_on_any_screen(self):
        """A thrown error stops every later listener on the page."""
        errors = []
        self.page.on("pageerror", lambda exc: errors.append(str(exc)))
        self.page.on("console", lambda msg: errors.append(msg.text)
                     if msg.type == "error" else None)
        sign_in(self.page, self.base)
        for path in ("/", "/queries", "/clusters/prod-a/queries",
                     "/clusters/prod-a/health", "/clusters/prod-b/health",
                     "/audit", "/account"):
            self.page.goto(self.base + path)
            self.page.wait_for_load_state()
        self.assertEqual([], errors)


if __name__ == "__main__":
    unittest.main()
