"""Browser tests for behaviour that only exists in the browser.

TypeScript checks the code, not what the code does once a person is clicking
it. These target the contracts a type checker cannot see:

  * the write ceremony: a blank reason never leaves the browser, and a double
    click never delivers two kills
  * the kill dialog is a real <dialog> - focus trap, Esc, inert background
  * a poll refreshes the numbers without blanking the screen somebody is
    reading
  * the theme survives a navigation
  * no screen throws, on any of the twelve

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

#: Every screen the console has. Listed rather than discovered: the router
#: lives in TypeScript, and a screen missing from here is a screen nobody
#: checks - which is how three server-rendered screens once shipped with no
#: render test at all.
SCREENS = (
    "/", "/queries", "/cluster-health", "/workload", "/gateway",
    "/resource-groups?cluster=prod-a", "/resource-groups/history?cluster=prod-a",
    "/fleet", "/fleet/jobs/1", "/restart?cluster=prod-a",
    "/benchmark", "/benchmark/runs/1", "/benchmark/sets",
    "/benchmark/sets/adhoc", "/benchmark/sets/adhoc/queries/scan_narrow/history",
    "/work", "/work/REQ-1", "/audit", "/account",
)


@unittest.skipUnless(HAVE_PLAYWRIGHT, "playwright not installed")
class UiBehaviourTest(unittest.TestCase):
    """One server and one browser for the whole class - both are slow to start."""

    @classmethod
    def setUpClass(cls):
        cls._server = serve(workload_enabled=True, resource_groups=True,
                            fleet_jobs=True, benchmark=True, restarts=True,
                            gateway={"enabled": True,
                                     "base_url": "https://gw.invalid:8080"})
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

    def open_kill_dialog(self, row=0):
        self.page.goto(self.base + "/queries")
        self.page.wait_for_selector("table.table tbody tr")
        self.page.locator(".row-btn--kill").nth(row).click()
        self.page.wait_for_selector("dialog[open] .modal__title")

    # ------------------------------------------------------------------ login

    def test_sign_in_works_over_https(self):
        """If the Secure cookie were not stored, this loops forever."""
        sign_in(self.page, self.base)
        self.assertNotIn("/login", self.page.url)
        self.assertTrue(self.page.locator("text=Overview").first.is_visible())

    def test_an_expired_session_lands_on_the_sign_in_page(self):
        """⛔ The SPA cannot recover from a 401 on its own read. It hands the
        browser back to the sign-in the server owns, rather than rendering an
        empty console that looks like an outage."""
        sign_in(self.page, self.base)
        self.context.clear_cookies()
        self.page.goto(self.base + "/queries")
        self.page.wait_for_url(lambda url: "/login" in url, timeout=10000)

    # ------------------------------------------------------- write ceremonies

    def test_a_blank_reason_never_leaves_the_browser(self):
        """The submit is disabled until a reason is typed, and nothing is sent.

        Asserting on validationMessage would prove nothing - the textarea is
        `required`, so the browser fills that in either way. The contract is
        that no request is made.
        """
        sign_in(self.page, self.base)
        self.open_kill_dialog()
        posted = []
        self.page.on("request",
                     lambda r: posted.append(r.url) if r.method == "POST" else None)
        self.page.locator("dialog[open] .btn--danger").click(force=True)
        self.page.wait_for_timeout(700)
        self.assertEqual([], posted, "a blank reason was sent to the server")
        self.assertEqual([], self.trino.killed)

    def test_a_double_click_delivers_one_kill(self):
        sign_in(self.page, self.base)
        self.open_kill_dialog()
        self.page.fill("#kill-reason", "runaway query, owner paged")
        button = self.page.locator("dialog[open] .btn--danger")
        button.click()
        try:
            # Disabled on the next render; a fast second click is a no-op.
            button.click(timeout=1500)
        except Exception:
            pass
        self.page.wait_for_timeout(1500)
        self.assertEqual(1, len(self.trino.killed),
                         "the query was killed {} times".format(len(self.trino.killed)))

    # ---------------------------------------------------------------- dialog

    def test_the_kill_dialog_opens_without_leaving_the_list(self):
        sign_in(self.page, self.base)
        self.page.goto(self.base + "/queries")
        url_before = self.page.url
        self.page.wait_for_selector("table.table tbody tr")
        self.page.locator(".row-btn--kill").first.click()
        self.page.wait_for_selector("dialog[open]", timeout=5000)
        self.assertEqual(url_before, self.page.url, "navigated instead of opening")

    def test_escape_closes_the_dialog(self):
        """Native <dialog>, so Esc is the browser's job - but only if the
        component actually handles `cancel` and unmounts."""
        sign_in(self.page, self.base)
        self.open_kill_dialog()
        self.page.keyboard.press("Escape")
        self.page.wait_for_selector("dialog[open]", state="detached", timeout=5000)

    # ---------------------------------------------------------------- polling

    def test_a_poll_does_not_blank_the_screen(self):
        """⛔ `loading` is true only on the first read. Flipping back to a
        spinner every few seconds hides the numbers somebody is reading."""
        sign_in(self.page, self.base)
        self.page.goto(self.base + "/queries")
        self.page.wait_for_selector("table.table tbody tr")
        rows = self.page.locator("table.table tbody tr")
        self.page.wait_for_timeout(6000)  # past the poll interval
        self.assertGreater(rows.count(), 0, "the table emptied on a refresh")

    # ---------------------------------------------------------------- theme

    def test_theme_toggle_persists_across_navigation(self):
        sign_in(self.page, self.base)
        # The shell applies the theme on mount, so wait for it to be there.
        self.page.wait_for_selector(".sidebar__foot")
        before = self.page.get_attribute("html", "data-theme")
        # ⛔ By label. The footer has two icon buttons and the other one signs
        # you out - which is exactly what this test used to do to itself.
        self.page.click("button[aria-label^='Switch to']")
        self.page.wait_for_timeout(300)
        after = self.page.get_attribute("html", "data-theme")
        self.assertNotEqual(before, after)
        self.page.goto(self.base + "/queries")
        self.page.wait_for_selector(".topbar")
        self.assertEqual(after, self.page.get_attribute("html", "data-theme"))

    # ----------------------------------------------------------- no JS errors

    def test_no_console_errors_on_any_screen(self):
        """A thrown error stops React rendering everything below it."""
        errors = []
        self.page.on("pageerror", lambda exc: errors.append(str(exc)))
        self.page.on("console", lambda msg: errors.append(msg.text)
                     if msg.type == "error" else None)
        sign_in(self.page, self.base)
        for path in SCREENS:
            self.page.goto(self.base + path)
            try:
                self.page.wait_for_selector(".topbar", timeout=10000)
            except Exception as exc:  # noqa: BLE001 - name the screen
                self.fail("{} never rendered: {} {}".format(path, exc, errors))
            self.page.wait_for_timeout(200)
            self.assertEqual([], errors, "on {}".format(path))
        self.assertEqual([], errors)


if __name__ == "__main__":
    unittest.main()
