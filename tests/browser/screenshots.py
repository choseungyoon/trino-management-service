"""Capture the console screens as images.

Not a test. This exists so the screens can be reviewed by someone who cannot
reach the deployment - which is most of the time, since TMS runs inside a
network the author is usually outside of.

Runs against the same harness the browser tests use: no PostgreSQL, no Trino.

    <venv>/bin/python -m tests.browser.screenshots [output-dir]
"""

import os
import sys

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.browser.harness import PASSWORD, USER, serve  # noqa: E402

#: (filename, path, what to do first). Each shot is a state worth being able to
#: look at, not just a page - the interesting ones are mid-interaction.
SHOTS = (
    ("01-tree", "/clusters/prod-a/resource-groups", None),
    ("02-edit-row", "/clusters/prod-a/resource-groups", "edit"),
    ("03-validation-refused", "/clusters/prod-a/resource-groups", "bad_edit"),
    ("04-saved", "/clusters/prod-a/resource-groups", "good_edit"),
    ("05-delete-impact", "/clusters/prod-a/resource-groups", "delete"),
    ("06-history", "/clusters/prod-a/resource-groups/history", None),
    ("07-not-loaded", "/clusters/prod-b/resource-groups", None),
    ("08-dark-theme", "/clusters/prod-a/resource-groups", "toggle_theme"),
    ("09-fleet-jobs", "/clusters/prod-a/fleet", None),
    ("10-job-log", "/fleet/jobs/1", None),
    ("11-work-board", "/work", None),
    ("12-work-item", "/work/REQ-1", None),
    ("13-work-decision", "/work/D-2", None),
    ("14-work-board-dark", "/work", "toggle_theme"),
    ("15-benchmark", "/benchmark", None),
    ("16-benchmark-dark", "/benchmark", "toggle_theme"),
    ("17-query-sets", "/benchmarks/sets", None),
    ("18-query-set", "/benchmarks/sets/adhoc", None),
    ("19-query-edit", "/benchmarks/sets/adhoc?edit=join_three", None),
    ("20-query-history", "/benchmarks/sets/adhoc/queries/scan_narrow/history", None),
    ("21-benchmark-run", "/benchmarks/1", None),
    ("22-benchmark-compare", "/benchmarks/2?against=1", None),
)


def _login(page, base_url):
    page.goto(base_url + "/login", wait_until="networkidle")
    page.fill("input[name=username]", USER)
    page.fill("input[name=password]", PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")


def _act(page, action):
    if action == "toggle_theme":
        page.click(".icon-btn")
        page.wait_for_timeout(300)
        return
    if action == "edit":
        page.click("#rg-2 button:has-text('Edit')")
        page.wait_for_selector("#rg-2 input[name=name]")
        return
    if action == "bad_edit":
        page.click("#rg-2 button:has-text('Edit')")
        page.wait_for_selector("#rg-2 input[name=name]")
        # 0 concurrency stops the group entirely. Trino accepts it; TMS does
        # not, because it is a delete wearing a tuning value's clothes.
        page.fill("#rg-2 input[name=hard_concurrency_limit]", "0")
        page.fill("#rg-2 input[name=reason]", "trying a zero limit")
        page.click("#rg-2 button:has-text('Save')")
        page.wait_for_selector("#rg-notices .banner--bad")
        return
    if action == "good_edit":
        page.click("#rg-2 button:has-text('Edit')")
        page.wait_for_selector("#rg-2 input[name=name]")
        page.fill("#rg-2 input[name=hard_concurrency_limit]", "12")
        page.fill("#rg-2 input[name=reason]",
                  "Superset dashboards were queueing behind one another")
        page.click("#rg-2 button:has-text('Save')")
        page.wait_for_selector("#rg-notices .banner--good")
        return
    if action == "delete":
        page.click("#rg-1 button:has-text('Delete')")
        page.wait_for_selector("#rg-1 .confirm")
        return


def main(out_dir):
    from playwright.sync_api import sync_playwright

    os.makedirs(out_dir, exist_ok=True)
    written = []

    with serve(workload_enabled=True, resource_groups=True,
               fleet_jobs=True, benchmark=True) as (base_url, _trino):
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for name, path, action in SHOTS:
                context = browser.new_context(
                    ignore_https_errors=True, viewport={"width": 1440, "height": 900},
                    device_scale_factor=2)
                page = context.new_page()
                errors = []
                page.on("console", lambda m: m.type == "error" and errors.append(m.text))
                _login(page, base_url)
                page.goto(base_url + path, wait_until="networkidle")
                if action:
                    _act(page, action)
                page.wait_for_timeout(250)
                target = os.path.join(out_dir, name + ".png")
                page.screenshot(path=target, full_page=True)
                written.append((name, target, errors))
                context.close()
            browser.close()

    for name, target, errors in written:
        print("{:24s} {}{}".format(
            name, target, "  CONSOLE ERRORS: {}".format(errors) if errors else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "scratchpad/screenshots"))
