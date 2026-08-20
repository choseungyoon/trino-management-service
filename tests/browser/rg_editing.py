"""Browser tests for the resource group editor (FR-WL-07~10).

This screen is the only one in TMS whose behaviour lives in htmx attributes
rather than in Python, so Python coverage says nothing about whether it works.
That gap has already cost twice: the selector routes shipped answering 422 and
revert shipped answering 500, both under a green suite, because nothing walked
a write path. The route sweep now catches the crude version of that; this
catches the part only a browser can see - that the right fragment is swapped
into the right place, and that a refusal comes back with the operator's typing
intact.

Not named `test_*`: `pytest tests/` must stay infrastructure-free, same
convention as ui_behaviour.py.

    <venv>/bin/python -m unittest tests.browser.rg_editing -v
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

#: The per-user leaf from the shipped template. Row 2 in the seeded tree.
LEAF = "#rg-2"


@unittest.skipUnless(HAVE_PLAYWRIGHT, "playwright not installed")
class ResourceGroupEditingTest(unittest.TestCase):
    """One server and one browser for the whole class - both are slow to start.

    The store is in-memory and shared, so each test restores what it changed
    rather than assuming a clean tree.
    """

    @classmethod
    def setUpClass(cls):
        cls._server = serve(workload_enabled=True, resource_groups=True)
        cls.base, cls.trino = cls._server.__enter__()
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()
        cls._server.__exit__(None, None, None)

    def setUp(self):
        self.context = self.browser.new_context(ignore_https_errors=True)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("console",
                     lambda m: m.type == "error" and self.errors.append(m.text))
        sign_in(self.page, self.base)
        self.open_screen()

    def tearDown(self):
        # A JavaScript error stops every later listener on the page, so a test
        # that passed after one is not evidence of anything.
        self.assertEqual([], self.errors, "console errors: {}".format(self.errors))
        self.context.close()

    # ----------------------------------------------------------------- helpers

    def open_screen(self):
        self.page.goto(self.base + "/clusters/prod-a/resource-groups",
                       wait_until="networkidle")

    def start_editing(self, row=LEAF):
        self.page.click("{} button:has-text('Edit')".format(row))
        # Waits for exactly what the assertions then look at. Waiting on the
        # input instead was flaky: the input is attached a beat before htmx has
        # finished settling the swapped row, so a `count()` immediately after
        # could still see the old one.
        self.page.wait_for_selector("tr{}.row-editing input[name=name]".format(row))

    def save(self, row=LEAF, **fields):
        for name, value in fields.items():
            self.page.fill("{} input[name={}]".format(row, name), str(value))
        self.page.click("{} button:has-text('Save')".format(row))

    def concurrency(self, row=LEAF):
        return self.page.inner_text(
            "{} td:nth-child(2)".format(row)).strip()

    # -------------------------------------------------------------- the swap

    def test_editing_swaps_only_the_row_it_was_asked_for(self):
        """The point of htmx here. A full reload would collapse the tree and
        throw away where the operator was looking."""
        self.start_editing()
        self.assertEqual(1, self.page.locator("tr.row-editing").count())
        self.assertTrue(self.page.locator("#rg-1 code").first.is_visible(),
                        "the other rows are still rendered as data")

    def test_cancel_puts_the_row_back_without_saving(self):
        self.start_editing()
        self.page.fill("{} input[name=hard_concurrency_limit]".format(LEAF), "99")
        self.page.click("{} button:has-text('Cancel')".format(LEAF))
        self.page.wait_for_selector("{} button:has-text('Edit')".format(LEAF))
        self.assertNotEqual("99", self.concurrency())

    # ------------------------------------------------------------- refusals

    def test_a_refused_save_keeps_what_was_typed(self):
        """⛔ This regressed once already: the row was redrawn from the store,
        so the rejected value vanished next to the message saying it was
        wrong. Nothing in Python noticed."""
        self.start_editing()
        self.save(hard_concurrency_limit=0, reason="trying a zero limit")
        self.page.wait_for_selector("#rg-notices .banner--bad")

        self.assertEqual(
            "0",
            self.page.input_value("{} input[name=hard_concurrency_limit]".format(LEAF)))
        self.assertEqual(
            "trying a zero limit",
            self.page.input_value("{} input[name=reason]".format(LEAF)))

    def test_the_refusal_says_what_was_wrong(self):
        self.start_editing()
        self.save(hard_concurrency_limit=0, reason="why")
        self.page.wait_for_selector("#rg-notices .banner--bad")
        self.assertIn("stops this group entirely",
                      self.page.inner_text("#rg-notices"))

    def test_a_blank_reason_never_reaches_the_server(self):
        """Required on the input as well as on the server: a round trip to be
        told the obvious is a round trip wasted."""
        self.start_editing()
        self.page.fill("{} input[name=hard_concurrency_limit]".format(LEAF), "11")
        self.page.fill("{} input[name=reason]".format(LEAF), "")
        self.page.click("{} button:has-text('Save')".format(LEAF))
        self.page.wait_for_timeout(300)
        self.assertEqual(1, self.page.locator("tr.row-editing").count(),
                         "still editing - nothing was submitted")

    # ---------------------------------------------------------------- saving

    def test_a_saved_value_lands_in_the_row_and_is_reported(self):
        self.start_editing()
        self.save(hard_concurrency_limit=12,
                  reason="dashboards were queueing behind one another")
        self.page.wait_for_selector("#rg-notices .banner--good")

        self.assertEqual("12", self.concurrency())
        self.assertIn("refresh-interval", self.page.inner_text("#rg-notices"),
                      "the screen says it is not instant")

        # Restore, so the shared tree is what the next test expects.
        self.start_editing()
        self.save(hard_concurrency_limit=8, reason="restoring for the next test")
        self.page.wait_for_selector("#rg-notices .banner--good")

    def test_the_change_survives_a_reload(self):
        """Proves the swap reflected a write rather than only the DOM."""
        self.start_editing()
        self.save(hard_concurrency_limit=9, reason="checking persistence")
        self.page.wait_for_selector("#rg-notices .banner--good")
        self.open_screen()
        self.assertEqual("9", self.concurrency())

        self.start_editing()
        self.save(hard_concurrency_limit=8, reason="restoring for the next test")
        self.page.wait_for_selector("#rg-notices .banner--good")

    # -------------------------------------------------------------- deleting

    def test_delete_lists_what_would_go_before_it_goes(self):
        """Both foreign keys cascade, so a count would understate it. The
        screen names each casualty."""
        self.page.click("#rg-1 button:has-text('Delete')")
        self.page.wait_for_selector("#rg-1 .confirm")
        text = self.page.inner_text("#rg-1 .confirm")
        self.assertIn("global.${USER}", text)
        self.assertIn("everything else", text, "the selector is named too")

        self.page.click("#rg-1 button:has-text('Cancel')")
        self.page.wait_for_selector("#rg-1 button:has-text('Edit')")

    # ------------------------------------------------------------- selectors

    def test_the_last_catch_all_is_offered_no_delete_button(self):
        """V10 is enforced on the server; this stops the offer being made.
        Trino 477 does not document what an unmatched query does."""
        rows = self.page.locator("#rg-selectors tbody tr")
        catch_all = rows.filter(has_text="everything else").first
        self.assertEqual(0, catch_all.locator("button:has-text('Delete')").count())
        self.assertIn("required", catch_all.inner_text())

    def test_adding_a_selector_swaps_the_selector_table_only(self):
        self.page.click("#rg-selectors summary:has-text('Add a selector')")
        self.page.fill("#rg-selectors input[name=pattern]", "^etl_.*$")
        self.page.fill("#rg-selectors input[name=reason]", "ETL gets its own rule")
        self.page.click("#rg-selectors button:has-text('Add selector')")
        self.page.wait_for_selector("#rg-notices .banner--good")

        self.assertIn("^etl_.*$", self.page.inner_text("#rg-selectors"))
        self.assertTrue(self.page.locator("#rg-tree").is_visible(),
                        "the group table was not disturbed")

    def test_deleting_a_selector_asks_first(self):
        """Two steps, like deleting a group. A reason box on every row would
        make the destructive action the loudest thing in the table."""
        rows = self.page.locator("#rg-selectors tbody tr")
        target = rows.filter(has_text="datalake").first
        target.locator("button:has-text('Delete')").click()
        self.page.wait_for_selector("#rg-selectors .confirm")
        self.assertIn("go to whichever rule matches next",
                      self.page.inner_text("#rg-selectors .confirm"))

        self.page.click("#rg-selectors button:has-text('Cancel')")
        self.page.wait_for_timeout(200)
        self.assertEqual(0, self.page.locator("#rg-selectors .confirm").count())


if __name__ == "__main__":
    unittest.main()
