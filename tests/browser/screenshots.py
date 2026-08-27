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
    ("01-overview", "/", None),
    ("02-overview-dark", "/", "toggle_theme"),
    ("03-queries", "/queries", None),
    ("04-kill", "/queries", "kill"),
    ("05-health", "/cluster-health", None),
    ("06-workload", "/workload", None),
    ("07-gateway", "/gateway", None),
    ("08-audit", "/audit", None),
    ("10-resource-groups", "/resource-groups?cluster=prod-a", None),
    ("11-rg-edit", "/resource-groups?cluster=prod-a", "rg_edit"),
    ("12-rg-refused", "/resource-groups?cluster=prod-a", "rg_bad_edit"),
    ("13-rg-delete", "/resource-groups?cluster=prod-a", "rg_delete"),
    ("14-rg-history", "/resource-groups/history?cluster=prod-a", None),
    ("20-fleet", "/fleet?cluster=prod-a", None),
    ("21-fleet-job", "/fleet/jobs/1", None),
    ("22-restart", "/restart?cluster=prod-a", None),
    ("30-benchmark", "/benchmark", None),
    ("31-benchmark-run", "/benchmark/runs/1", None),
    ("32-query-sets", "/benchmark/sets", None),
    ("33-query-set", "/benchmark/sets/adhoc", None),
    ("34-query-history",
     "/benchmark/sets/adhoc/queries/scan_narrow/history", None),
    ("35-query-history-daily",
     "/benchmark/sets/adhoc/queries/scan_narrow/history?bucket=day", None),
    ("37-schedules", "/benchmark/schedules", None),
    ("36-query-history-one-cluster",
     "/benchmark/sets/adhoc/queries/scan_narrow/history", "hide_series"),
    ("40-work-board", "/work", None),
    ("41-work-item", "/work/REQ-1", None),
    ("42-work-decision", "/work/D-2", None),
    ("43-work-board-dark", "/work", "toggle_theme"),
    # ⛔ Last. Starting a sequence leaves the cluster out of rotation, and the
    # shell then draws the restart banner on every screen shot afterwards.
    ("90-restart-draining", "/restart?cluster=prod-a", "restart_begin"),
)


def _login(page, base_url):
    page.goto(base_url + "/login", wait_until="networkidle")
    page.fill("input[name=username]", USER)
    page.fill("input[name=password]", PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_url(lambda url: "/login" not in url, timeout=10000)
    page.wait_for_load_state("networkidle")


def _act(page, action):
    if action == "kill":
        page.wait_for_selector("table.table tbody tr")
        page.click(".row-btn--kill")
        page.wait_for_selector("dialog[open] .modal__title")
        page.fill("#kill-reason", "blocking the nightly load for 40 minutes")
        page.wait_for_timeout(150)
        return
    if action == "restart_begin":
        # prod-a has queries running, so the sequence stops at the drain -
        # the state where the screen has the most to say.
        page.fill("#reason", "applying the new memory configuration from CHG-4471")
        page.click("button:has-text('Begin the restart sequence')")
        page.wait_for_selector(".seq__act-why")
        page.wait_for_timeout(400)
        return
    if action == "rg_edit":
        page.click("#rg-2 button:has-text('Edit')")
        page.wait_for_selector("#rg-2 input[aria-label='Group name']")
        return
    if action == "rg_bad_edit":
        page.click("#rg-2 button:has-text('Edit')")
        page.wait_for_selector("#rg-2 input[aria-label='Group name']")
        # 0 concurrency stops the group entirely. Trino accepts it; TMS does
        # not, because it is a delete wearing a tuning value's clothes.
        page.fill("#rg-2 input[aria-label='Concurrency limit']", "0")
        page.fill("#rg-2 input[aria-label='Reason']", "trying a zero limit")
        page.click("#rg-2 button:has-text('Save')")
        page.wait_for_selector(".banner--bad")
        return
    if action == "rg_delete":
        page.click("#rg-1 button:has-text('Delete')")
        page.wait_for_selector("#rg-1 .confirm__impact")
        return
    if action == "hide_series":
        page.wait_for_selector(".chart__key")
        page.locator(".chart__key").first.click()
        page.wait_for_timeout(250)
        return
    if action == "toggle_theme":
        page.click("button[aria-label^='Switch to']")
        page.wait_for_timeout(300)
        return


def main(out_dir):
    from playwright.sync_api import sync_playwright

    os.makedirs(out_dir, exist_ok=True)
    written = []

    with serve(workload_enabled=True, resource_groups=True,
               fleet_jobs=True, benchmark=True, restarts=True,
               # On, so the Gateway screen shows its tables rather than only
               # the "integration is off" banner.
               gateway={"enabled": True, "base_url": "https://gw.invalid:8080"},
               ) as (base_url, _trino):
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
