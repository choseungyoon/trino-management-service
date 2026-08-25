"""The safe restart screen, driven through ASGI (FR-CO-02).

The state machine and the service have their own tests. What is checked here is
the part an operator actually touches: that each step is reachable only through
its own POST, that the screen says what it is waiting for, that the live
fragment carries the progress log, and - most importantly - that no route
offers a way around the order CLAUDE.md rule 5 fixes.

The Gateway and the executor are stubs. The point is the screens and the wiring,
not the transports, which are covered in tests/test_gateway_client.py and
tests/test_ansible_executor.py.
"""

import os
import sys
import unittest
from contextlib import asynccontextmanager

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))
# The harness in test_web_routes builds a fully wired service; importing it
# beats a second copy that would drift from the one the other screens use.
sys.path.insert(0, _HERE)

try:
    import httpx
    from fastapi import FastAPI  # noqa: F401
    from jinja2 import Environment  # noqa: F401
    import multipart  # noqa: F401

    WEB_DEPS = True
except ImportError:  # pragma: no cover - environment dependent
    WEB_DEPS = False

from tms.api.main import create_app  # noqa: E402
from tms.collector.snapshot import (  # noqa: E402
    GATEWAY_SCOPE,
    KIND_GATEWAY,
    KIND_HEALTH,
    KIND_QUERIES,
    Snapshot,
    utcnow,
)
from tms.ops.executor import (  # noqa: E402
    PENDING_OPERATOR,
    RUNNING,
    SUCCEEDED,
    ManualExecutor,
)
from tms.ops.repository import InMemorySequenceRepository  # noqa: E402
from tms.ops.sequence import RestartSequence as _sequence  # noqa: E402
from tms.ops.service import RestartService  # noqa: E402

from test_web_routes import PASSWORD, USER, build_service, client_for, sign_in  # noqa: E402


class StubGateway:
    """Records activation changes the way the real client applies them."""

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def set_active(self, name, active):
        if self.fail_on is not None and len(self.calls) == self.fail_on:
            raise RuntimeError("gateway refused")
        self.calls.append((name, active))


class StubExecutor:
    """An automated executor whose output and outcome the test drives."""

    name = "stub"

    def __init__(self, automated=True, lines=None, state=RUNNING):
        self.automated = automated
        self.lines = list(lines or [])
        self.state = state
        self.started = []

    def start(self, cluster, sequence_id):
        self.started.append((cluster, sequence_id))
        return RUNNING if self.automated else PENDING_OPERATOR

    def status(self, cluster, sequence_id):
        return self.state

    def lines_since(self, sequence_id, index=0):
        return self.lines[index:]

    def describe(self, cluster):
        return {"automated": self.automated,
                "title": "Restart {} now".format(cluster),
                "instructions": "Do the thing.",
                "waiting": "Waiting on the stub."}


def build_app(roles=("admin",), gateway=None, executor=None, running=1,
              health="GOOD"):
    config, service, _trino = build_service(roles=roles)
    now = utcnow()
    service.repository.save(Snapshot("prod-a", KIND_QUERIES, now, payload={
        "summary": {"running": running, "queued": 0, "total": running},
        "queries": [],
    }))
    service.repository.save(Snapshot("prod-a", KIND_HEALTH, now, payload={
        "rollup_state": health, "rollup_enabled": True, "tests": [],
    }))
    # The Gateway backend name deliberately differs from the TMS cluster name:
    # matching is by URL, and a screen that guessed would deactivate the wrong
    # backend.
    service.repository.save(Snapshot(GATEWAY_SCOPE, KIND_GATEWAY, now, payload={
        "backends": [{"name": "trino-prod-a-1", "cluster": "prod-a",
                      "active": True, "routing_group": "adhoc"}],
    }))

    gateway = gateway or StubGateway()
    restarts = RestartService(
        config=config, repository=InMemorySequenceRepository(),
        snapshots=service.repository, gateway_client=gateway,
        audit_guard=service.audit, executor=executor or StubExecutor(automated=False),
    )
    app = create_app(config=config, service=service, restarts=restarts)
    return app, restarts, gateway


@unittest.skipUnless(WEB_DEPS, "fastapi/httpx/jinja2/python-multipart not installed")
class RestartScreenTest(unittest.IsolatedAsyncioTestCase):
    @asynccontextmanager
    async def session(self, **kwargs):
        """A signed-in admin against a freshly wired app."""
        app, restarts, gateway = build_app(**kwargs)
        async with client_for(app) as client:
            await sign_in(client)
            yield client, restarts, gateway

    async def _start(self, client, reason="applying CHG-4471"):
        return await client.post("/clusters/prod-a/restart", data={"reason": reason})

    # ------------------------------------------------------------- the screen

    async def test_the_start_page_shows_the_whole_sequence_before_it_begins(self):
        """Someone about to take a cluster out of rotation should see what TMS
        will do before they commit, not discover it a button at a time."""
        async with self.session() as (client, _r, _g):
            body = (await client.get("/clusters/prod-a/restart")).text
        for phrase in ("Stop new queries reaching it", "Wait for every running query",
                       "Verify health is GOOD", "Put it back in rotation"):
            self.assertIn(phrase, body)

    async def test_a_reason_is_required_to_begin(self):
        async with self.session() as (client, restarts, gateway):
            response = await self._start(client, reason="   ")
        self.assertEqual(400, response.status_code)
        self.assertEqual([], gateway.calls, "traffic was not touched")
        self.assertEqual([], restarts.active())

    async def test_beginning_stops_traffic_and_lands_on_the_sequence(self):
        async with self.session() as (client, restarts, gateway):
            response = await self._start(client)
            self.assertEqual(303, response.status_code)
            body = (await client.get(response.headers["location"])).text

        self.assertEqual([("trino-prod-a-1", False)], gateway.calls,
                         "the backend is chosen by URL match, not by name")
        self.assertIn("Waiting for 1 running query to finish.", body)
        self.assertIn("No traffic", body)

    async def test_a_non_admin_cannot_begin_one(self):
        async with self.session(roles=("viewer",)) as (client, restarts, gateway):
            response = await self._start(client)
        self.assertEqual(403, response.status_code)
        self.assertEqual([], gateway.calls)

    async def test_a_viewer_can_watch_but_is_offered_no_controls(self):
        """A restart in progress is something everyone needs to see; advancing
        it is not something everyone may do."""
        async with self.session(roles=("viewer",)) as (client, restarts, _g):
            # Started by someone else - a viewer has no route to begin one.
            restarts.repository.create(
                _sequence("prod-a", "applying CHG-4471", "admin1"))
            body = (await client.get("/clusters/prod-a/restart")).text

        self.assertNotIn("Begin the restart sequence", body)
        self.assertIn("an administrator can advance the sequence", body)
        self.assertNotIn("Abort and restore traffic", body)

    # -------------------------------------------------------------- the order

    async def test_the_restart_step_is_refused_while_queries_are_running(self):
        """The gate that actually prevents the incident."""
        async with self.session(running=3) as (client, restarts, _g):
            await self._start(client)
            sequence_id = restarts.active()[0]["id"]
            response = await client.post("/restarts/{}/restart".format(sequence_id))
            self.assertEqual(303, response.status_code)
            body = (await client.get("/restarts/{}".format(sequence_id))).text

        self.assertEqual("DRAINING", restarts.repository.load(sequence_id).sequence.state)
        self.assertIn("3 queries are still running", body)

    async def test_traffic_is_not_restored_until_health_is_good(self):
        async with self.session(running=0, health="BAD") as (client, restarts, gateway):
            await self._start(client)
            sequence_id = restarts.active()[0]["id"]
            await client.post("/restarts/{}/restart".format(sequence_id))
            await client.post("/restarts/{}/restarted".format(sequence_id))
            response = await client.post("/restarts/{}/complete".format(sequence_id))
            body = (await client.get("/restarts/{}".format(sequence_id))).text

        self.assertEqual("VERIFYING", restarts.repository.load(sequence_id).sequence.state)
        self.assertEqual([("trino-prod-a-1", False)], gateway.calls,
                         "the backend was never reactivated")
        self.assertIn("traffic is not restored", body.lower())

    async def test_the_whole_sequence_completes_and_restores_traffic(self):
        async with self.session(running=0, health="GOOD") as (client, restarts, gateway):
            await self._start(client)
            sequence_id = restarts.active()[0]["id"]
            await client.post("/restarts/{}/restart".format(sequence_id))
            await client.post("/restarts/{}/restarted".format(sequence_id))
            await client.post("/restarts/{}/complete".format(sequence_id))
            body = (await client.get("/restarts/{}".format(sequence_id))).text

        self.assertEqual([("trino-prod-a-1", False), ("trino-prod-a-1", True)],
                         gateway.calls)
        self.assertEqual([], restarts.active(), "the sequence is finished")
        self.assertIn("back in rotation", body)

    async def test_forcing_past_the_drain_needs_its_own_reason(self):
        async with self.session(running=2) as (client, restarts, _g):
            await self._start(client)
            sequence_id = restarts.active()[0]["id"]
            await client.post("/restarts/{}/force-drain".format(sequence_id),
                              data={"reason": ""})
            self.assertEqual("DRAINING",
                             restarts.repository.load(sequence_id).sequence.state)

            await client.post("/restarts/{}/force-drain".format(sequence_id),
                              data={"reason": "query stuck for 40 minutes"})
            stored = restarts.repository.load(sequence_id)
            body = (await client.get("/restarts/{}".format(sequence_id))).text

        self.assertEqual("DRAINED", stored.sequence.state)
        self.assertIn("FORCED past the drain with 2 queries", body)

    async def test_aborting_restores_traffic_rather_than_just_stopping(self):
        """An abandoned sequence leaves a cluster receiving nothing - a quiet
        outage, because every other cluster is green."""
        async with self.session() as (client, restarts, gateway):
            await self._start(client)
            sequence_id = restarts.active()[0]["id"]
            await client.post("/restarts/{}/abort".format(sequence_id),
                              data={"reason": "change called off"})

        self.assertEqual([("trino-prod-a-1", False), ("trino-prod-a-1", True)],
                         gateway.calls)
        self.assertEqual("ABORTED", restarts.repository.load(sequence_id).sequence.state)

    async def test_a_failed_reactivation_keeps_the_sequence_visible(self):
        """Still holding traffic back, so it must not disappear from the UI."""
        gateway = StubGateway(fail_on=1)
        async with self.session(gateway=gateway) as (client, restarts, _g):
            await self._start(client)
            sequence_id = restarts.active()[0]["id"]
            await client.post("/restarts/{}/abort".format(sequence_id),
                              data={"reason": "change called off"})
            body = (await client.get("/")).text

        self.assertEqual("ABORTING", restarts.repository.load(sequence_id).sequence.state)
        self.assertIn("prod-a is being restarted", body)

    # ----------------------------------------------------------- the live view

    async def test_the_banner_follows_the_operator_onto_other_screens(self):
        async with self.session() as (client, _r, _g):
            await self._start(client)
            for path in ("/", "/queries", "/audit"):
                body = (await client.get(path)).text
                self.assertIn("prod-a is being restarted", body, path)
                self.assertIn("receiving no queries", body, path)

    async def test_history_rows_say_when_the_restart_happened(self):
        """"Who and why" without "when" is not a record anyone can use."""
        async with self.session(running=0, health="GOOD") as (client, restarts, _g):
            await self._start(client)
            sequence_id = restarts.active()[0]["id"]
            await client.post("/restarts/{}/restart".format(sequence_id))
            await client.post("/restarts/{}/restarted".format(sequence_id))
            await client.post("/restarts/{}/complete".format(sequence_id))
            stored = restarts.repository.load(sequence_id)
            body = (await client.get("/clusters/prod-a/restart")).text

        self.assertIsNotNone(stored.started_at)
        self.assertIsNotNone(stored.finished_at, "a finished sequence records when")
        self.assertIn("<th>Started</th>", body)

    async def test_manual_mode_tells_the_operator_it_is_their_turn(self):
        """The first real run stalled here: the screen showed only a "it is back
        up" button, so the operator pressed "restart", saw nothing happen, and
        reasonably concluded TMS had failed. At RESTARTING with a manual
        executor the screen must say TMS is not restarting anything."""
        async with self.session(running=0, executor=ManualExecutor()) \
                as (client, restarts, _g):
            await self._start(client)
            sequence_id = restarts.active()[0]["id"]

            drained = (await client.get("/restarts/{}".format(sequence_id))).text
            self.assertIn("I will restart prod-a myself", drained,
                          "the button must not read as an instruction to TMS")
            self.assertNotIn("Restart prod-a now", drained)

            await client.post("/restarts/{}/restart".format(sequence_id))
            restarting = (await client.get("/restarts/{}".format(sequence_id))).text

        self.assertIn("Your turn", restarting)
        self.assertIn("TMS is not restarting anything", restarting)
        self.assertIn("Restart prod-a now, using your normal procedure", restarting)
        # And the log line says the same thing rather than "waiting for the
        # operator", which was read as TMS working.
        self.assertIn("TMS is NOT restarting prod-a", restarting)

    async def test_an_automated_restart_does_not_ask_the_operator_to_act(self):
        """The mirror image: with Ansible driving it, telling the operator to
        go and restart the cluster by hand would cause a double restart."""
        async with self.session(running=0, executor=StubExecutor(automated=True)) \
                as (client, restarts, _g):
            await self._start(client)
            sequence_id = restarts.active()[0]["id"]
            await client.post("/restarts/{}/restart".format(sequence_id))
            body = (await client.get("/restarts/{}".format(sequence_id))).text

        self.assertNotIn("Your turn", body)
        self.assertIn("The playbook is running", body)

    async def test_a_failed_playbook_stops_claiming_it_is_running(self):
        """Reported from the first ansible run: the log showed
        `[Errno 2] No such file or directory: 'ansible-playbook'` while the
        panel beside it still said "The playbook is running", so the operator
        had no reason to think anything needed doing."""
        from tms.ops.executor import FAILED

        executor = StubExecutor(automated=True, state=FAILED)
        async with self.session(running=0, executor=executor) as (client, restarts, _g):
            await self._start(client)
            sequence_id = restarts.active()[0]["id"]
            await client.post("/restarts/{}/restart".format(sequence_id))
            body = (await client.get("/restarts/{}".format(sequence_id))).text

        self.assertNotIn("The playbook is running", body)
        self.assertIn("The restart failed", body)
        self.assertIn("nothing was restarted", body)
        # And the advice names a control that exists. Re-running the restart is
        # refused from RESTARTING, so it must not be suggested.
        self.assertIn("Put it back in rotation", body)

    async def test_the_failure_advice_does_not_send_them_to_a_missing_button(self):
        """`restart` is refused from RESTARTING. The log line used to say "run
        the restart again", which is a control that is not on the screen."""
        from tms.ops.executor import FAILED

        async with self.session(running=0,
                                executor=StubExecutor(automated=True, state=FAILED)) \
                as (client, restarts, _g):
            await self._start(client)
            sequence_id = restarts.active()[0]["id"]
            await client.post("/restarts/{}/restart".format(sequence_id))
            # The failure is noticed on the next observation, which is what the
            # live view does every couple of seconds.
            await client.get("/restarts/{}".format(sequence_id))
            # Confirm the retry really is refused, so the wording matters.
            again = await client.post("/restarts/{}/restart".format(sequence_id))
            history = restarts.repository.load(sequence_id).sequence.history

        self.assertEqual(303, again.status_code)
        failure = [h["message"] for h in history if h["level"] == "error"]
        self.assertTrue(failure)
        self.assertNotIn("run the restart again", " ".join(failure))
        self.assertIn("Abort to put it back", " ".join(failure))

    async def test_the_fragment_carries_the_log_without_the_page_chrome(self):
        async with self.session() as (client, restarts, _g):
            await self._start(client)
            sequence_id = restarts.active()[0]["id"]
            response = await client.get("/restarts/{}?fragment=1".format(sequence_id))

        self.assertEqual(200, response.status_code)
        self.assertIn('id="console"', response.text)
        self.assertIn("Blocking new queries to prod-a", response.text)
        self.assertNotIn("<html", response.text, "the fragment replaces two panels")

    async def test_playbook_output_reaches_the_log_and_is_marked_as_output(self):
        executor = StubExecutor(lines=["PLAY [restart trino] ***",
                                       "TASK [stop worker 1] ***"])
        async with self.session(running=0, executor=executor) as (client, restarts, _g):
            await self._start(client)
            sequence_id = restarts.active()[0]["id"]
            await client.post("/restarts/{}/restart".format(sequence_id))
            body = (await client.get("/restarts/{}?fragment=1".format(sequence_id))).text

        self.assertIn("TASK [stop worker 1] ***", body)
        self.assertIn("console__line--output", body,
                      "verbatim playbook output is not styled as TMS prose")

    async def test_output_is_not_recorded_twice_when_the_view_is_polled(self):
        """The live view reloads the sequence on every poll; an in-memory
        cursor would restart at zero and duplicate every line."""
        executor = StubExecutor(lines=["PLAY [restart trino] ***"])
        async with self.session(running=0, executor=executor) as (client, restarts, _g):
            await self._start(client)
            sequence_id = restarts.active()[0]["id"]
            await client.post("/restarts/{}/restart".format(sequence_id))
            for _ in range(4):
                await client.get("/restarts/{}?fragment=1".format(sequence_id))
            history = restarts.repository.load(sequence_id).sequence.history

        occurrences = [h for h in history if h["message"] == "PLAY [restart trino] ***"]
        self.assertEqual(1, len(occurrences), history)

    async def test_a_finished_playbook_advances_the_sequence_by_itself(self):
        executor = StubExecutor(lines=["PLAY RECAP ***"], state=SUCCEEDED)
        async with self.session(running=0, executor=executor) as (client, restarts, _g):
            await self._start(client)
            sequence_id = restarts.active()[0]["id"]
            await client.post("/restarts/{}/restart".format(sequence_id))
            await client.get("/restarts/{}?fragment=1".format(sequence_id))

        self.assertEqual("VERIFYING",
                         restarts.repository.load(sequence_id).sequence.state)

    async def test_a_repeated_step_post_cannot_replay_it(self):
        """A stale tab must not fire step 4 at a cluster that has moved on."""
        async with self.session(running=0) as (client, restarts, _g):
            await self._start(client)
            sequence_id = restarts.active()[0]["id"]
            await client.post("/restarts/{}/restart".format(sequence_id))
            await client.post("/restarts/{}/restart".format(sequence_id))
            stored = restarts.repository.load(sequence_id)

        self.assertEqual("RESTARTING", stored.sequence.state)

    async def test_a_second_restart_of_the_same_cluster_is_refused(self):
        async with self.session() as (client, restarts, gateway):
            await self._start(client)
            response = await self._start(client, reason="another change")
        self.assertEqual(400, response.status_code)
        self.assertEqual(1, len(gateway.calls), "traffic was stopped once")

    # ------------------------------------------------------- when it is off

    async def test_without_a_gateway_the_page_says_why_rather_than_offering_a_button(self):
        config, service, _trino = build_service()
        app = create_app(config=config, service=service, restarts=None)
        async with client_for(app) as client:
            await sign_in(client)
            body = (await client.get("/clusters/prod-a/restart")).text
            nav = (await client.get("/")).text

        self.assertIn("Restarts are not available", body)
        self.assertNotIn("Begin the restart sequence", body)
        self.assertNotIn("Safe Restart", nav, "the nav hides a link that cannot work")


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(WEB_DEPS, "fastapi/httpx/jinja2/python-multipart not installed")
class EveryScreenTest(unittest.IsolatedAsyncioTestCase):
    """Render every UI route with the integrations switched ON.

    Written after `/gateway` returned 500 for every request while 563 tests
    passed. The web tests only ever built an app with Gateway, workload, fleet
    and restarts *disabled*, so each of those screens returned early from its
    service and never reached the code that was broken.

    The route list is taken from the app itself rather than typed out here. A
    hand-written list is how `/gateway`, `/workload` and `/fleet` came to have
    no render test at all: someone adds a screen and does not think to add it
    twice.
    """

    #: Values for path parameters. A route whose parameter is not here fails
    #: loudly - that is the prompt to add it, not a reason to skip the route.
    PARAMS = {
        "cluster": "prod-a",
        "query_id": "20260808_000000_00001_abcde",
        "test_id": "H-01",
        "sequence_id": "1",
        "host": "w1",
        # Resource group rows are addressed by their id in Trino's table. The
        # sweep only checks the route renders; a row that does not exist still
        # has to answer with a page rather than a traceback.
        "row_id": "1",
        "selector_id": "1",
        "revision_id": "1",
        "run_id": "1",
        # A work item that the seeded board actually has, so the detail page
        # renders its timeline rather than a 404 the sweep would accept.
        "key": "W-1",
        # The benchmark query set the harness below seeds, and the query in it.
        # `set_key` rather than `key` precisely so the two cannot collide: a
        # board key is uppercase and a set key may not be, so one value could
        # never render both screens.
        "set_key": "smoke",
        "name": "scan",
    }

    #: Routes that legitimately answer with something other than 200.
    EXPECTED = {
        "/clusters": 303,
        # Already signed in, so the login page redirects on. Correct.
        "/login": 303,
    }

    #: Not screens. `/logout` in particular would end the session part-way
    #: through the sweep and turn every later route into a redirect to /login -
    #: which looks like the sweep passing over pages it never rendered.
    NOT_SCREENS = ("/logout",)

    #: Not write routes in the sense this sweep means. `/login` would replace
    #: the session mid-run and turn every later route into a redirect; the
    #: password change would do the same by invalidating it.
    NOT_WRITES = ("/login", "/logout", "/account/password")

    #: One body for every write route. Deliberately a superset - each route
    #: reads the fields it knows and ignores the rest, so a single dict covers
    #: all of them without the sweep needing to know what any route wants.
    #: `reason` is the one field they all share, because every write in TMS
    #: requires one (absolute rule 3).
    WRITE_BODY = {
        "reason": "route sweep",
        "message": "route sweep",
        "name": "sweep",
        "priority": "5",
        "matcher": "user_regex",
        "pattern": "^sweep$",
        "target_row_id": "1",
        "parent_row_id": "",
        "hard_concurrency_limit": "10",
        "max_queued": "100",
        "soft_memory_limit": "10%",
        "scheduling_policy": "fair",
        "state": "on",
        "value": "1",
        "theme": "light",
        # The work board's own fields. `body` doubles as the comment text and
        # `status` as the board move; both are ignored by every other route.
        "title": "route sweep",
        "body": "route sweep",
        "status": "planned",
        "note": "route sweep",
        # The benchmark start form. `reason` above is already in the body.
        "query_set": "smoke",
        "repetitions": "1",
        # Query set editing (FR-BM-06). `name` above doubles as the query name.
        "set_key": "smoke",
        "statement": "SELECT 1",
        "position": "0",
        "original_name": "",
    }

    def _app(self):
        from tms.collector.snapshot import GATEWAY_SCOPE, KIND_GATEWAY, KIND_RESOURCE_GROUPS

        config, service, _trino = build_service(
            workload={"enabled": True, "poll_interval_seconds": 15})
        now = utcnow()
        service.repository.save(Snapshot(GATEWAY_SCOPE, KIND_GATEWAY, now, payload={
            "backends": [{"name": "trino-prod-a-1", "cluster": "prod-a",
                          "active": True, "routing_group": "adhoc",
                          "proxy_to": "https://a.invalid:8443"}],
            "groups": [{"name": "adhoc", "active": 1, "backends": ["trino-prod-a-1"]}],
            "unmonitored_backends": [], "unrouted_clusters": [],
            "routing_rules": [{"priority": 1, "name": "r", "condition": "true",
                               "actions": ["adhoc"]}],
            "live": True,
        }))
        service.repository.save(Snapshot("prod-a", KIND_RESOURCE_GROUPS, now, payload={
            "tree": [{"id": "global", "name": "global", "depth": 0, "running": 1,
                      "queued": 0, "children": []}],
            "groups": [{"id": "global", "name": "global", "depth": 0, "running": 1,
                        "queued": 0, "cpu_ms": 10.0, "memory_bytes": 1024}],
            "summary": {"groups": 1, "running": 1, "queued": 0, "blocked_groups": 0,
                        "blocked": []},
            "complete": False,
        }))
        service.repository.save(Snapshot(GATEWAY_SCOPE, KIND_GATEWAY, now,
                                         payload=service.repository.load(
                                             GATEWAY_SCOPE, KIND_GATEWAY).payload))
        restarts = RestartService(
            config=config, repository=InMemorySequenceRepository(),
            snapshots=service.repository, gateway_client=StubGateway(),
            audit_guard=service.audit, executor=StubExecutor(automated=False))
        # One sequence so /restarts/{id} has something to render.
        restarts.repository.create(_sequence("prod-a", "rendering test", "syhcho"))

        from tms.collector.snapshot import KIND_FLEET
        from tms.fleet.service import FleetService

        service.repository.save(Snapshot("prod-a", KIND_FLEET, now, payload={
            "nodes": [{"host": "w1", "address": "w1", "role": "worker",
                       "cluster": "prod-a", "reachable": True, "state": "ACTIVE",
                       "version": "477", "environment": "prod", "uptime": "1d",
                       "coordinator": False, "error": None}],
            "summary": {"total": 1, "reachable": 1, "unreachable": 0,
                        "workers": 1, "shutting_down": 0},
            "notes": [], "node_counts": {"ActiveNodeCount": 2}, "inventory_size": 1,
        }))
        # A configured job and one finished run, so the sweep renders the job
        # panel and the run page rather than skipping past both. Without this
        # the routes exist and nothing ever draws them.
        from tms.fleet.jobs import JobRunner, build_jobs
        from tms.fleet.jobstore import InMemoryJobRepository

        job_definitions = build_jobs({
            "scale_out": {"playbook": __file__, "title": "Add workers",
                          "parameters": {"count": {"min": 1, "max": 4, "default": 2}}},
        })
        job_repository = InMemoryJobRepository()
        seeded = job_repository.create("prod-a", "scale_out", "syhcho", ["admin"],
                                       "rendering test", {"count": 2})
        job_repository.append_output(seeded["id"], "PLAY [add workers]")
        job_repository.finish(seeded["id"], "SUCCEEDED", exit_code=0)

        fleet = FleetService(
            job_runner=JobRunner(jobs=job_definitions,
                                 cluster_inventories={"prod-a": __file__},
                                 runner=lambda *a, **k: {"rc": 0}),
            job_repository=job_repository,
            config=config, snapshots=service.repository, audit_guard=service.audit,
            transport_factory=lambda: None)
        # The benchmark harness with one finished run, so /clusters/{c}/benchmark
        # and /benchmarks/{id} draw a table rather than the empty state. The
        # Gateway stub reports the backend deactivated, which is the only state
        # in which the guard lets the start form be usable at all (FR-BM-04).
        from tms.bench.queryset import build_query_sets
        from tms.bench.runner import BenchmarkRunner
        from tms.bench.service import BenchmarkService
        from tms.bench.store import InMemoryBenchmarkRepository

        bench_repository = InMemoryBenchmarkRepository()
        seeded_run = bench_repository.create(
            cluster="prod-a", query_set="smoke", actor="syhcho", roles=["admin"],
            reason="rendering test", repetitions=2, guard={"ok": True},
            label="baseline")
        for iteration in (1, 2):
            bench_repository.add_result(seeded_run["id"], {
                "query_name": "scan", "iteration": iteration, "state": "SUCCEEDED",
                "trino_query_id": "20260821_000000_0000{}_abcde".format(iteration),
                "elapsed_ms": 1200 + iteration, "trino_elapsed_ms": 1100,
                "trino_cpu_ms": 900, "trino_queued_ms": 3, "trino_planning_ms": 40,
                "processed_rows": 15000, "processed_bytes": 4096,
                "peak_memory_bytes": 8192, "error": None})
        bench_repository.finish(seeded_run["id"], "SUCCEEDED")

        class DeactivatedGateway:
            @staticmethod
            def list_backends(active_only=False):
                return [{"name": "trino-prod-a-1", "active": False}]

        benchmark = BenchmarkService(
            config=config, snapshots=service.repository, audit_guard=service.audit,
            repository=bench_repository,
            runner=BenchmarkRunner(sql_client_factory=lambda c: None,
                                   repository=bench_repository),
            query_sets=build_query_sets({
                "smoke": {"title": "Smoke",
                          "queries": [{"name": "scan", "sql": "SELECT 1"}]}}),
            gateway_client=DeactivatedGateway())

        # A seeded board, so /work and /work/{key} draw real columns and a real
        # timeline instead of the empty state.
        from tms.work.seed import seed as seed_board
        from tms.work.service import BoardService
        from tms.work.store import InMemoryBoardRepository

        board_repository = InMemoryBoardRepository()
        seed_board(board_repository)
        board_repository.add_comment("W-1", "syhcho", "rendering test")

        return create_app(config=config, service=service, restarts=restarts,
                          fleet=fleet, board=BoardService(board_repository),
                          benchmark=benchmark), config

    async def test_every_ui_screen_renders_with_the_integrations_on(self):
        app, _config = self._app()
        paths = []
        for route in app.routes:
            path = getattr(route, "path", "")
            methods = getattr(route, "methods", set()) or set()
            if "GET" not in methods or path.startswith(("/api/", "/ui/static")):
                continue
            if path in ("/health", "/ready", "/metrics") or path in self.NOT_SCREENS:
                continue
            missing = [p for p in _path_params(path) if p not in self.PARAMS]
            self.assertEqual(
                [], missing,
                "route {} has path parameter(s) {} with no test value - add "
                "them to EveryScreenTest.PARAMS so the screen is covered"
                .format(path, missing))
            paths.append(path)

        self.assertGreater(len(paths), 8, "route discovery found almost nothing")

        client = client_for(app)
        async with client:
            await sign_in(client)
            for path in paths:
                url = path
                for name, value in self.PARAMS.items():
                    url = url.replace("{" + name + "}", value)
                response = await client.get(url)
                self.assertEqual(
                    self.EXPECTED.get(path, 200), response.status_code,
                    "{} returned {}".format(url, response.status_code))

    async def test_no_write_route_answers_with_a_traceback(self):
        """Every POST, with a plausible body and a reason.

        The GET sweep above left every write route uncovered, and two of them
        shipped broken: the resource group selector routes answered 422 because
        a literal path segment was registered after an int-typed `{row_id}` and
        got parsed as one, and revert answered 500 because its success message
        contained an em dash - cookies are latin-1.

        This does not assert that the action *worked*; the feature tests do
        that. It asserts the far weaker thing that was missing entirely - that
        the route exists, is reachable, and fails in a way a person can read.
        A 4xx is a fine outcome here: it means the request was understood and
        refused. A 5xx or a 422 from path parsing is not.
        """
        app, _config = self._app()
        posts = []
        for route in app.routes:
            path = getattr(route, "path", "")
            methods = getattr(route, "methods", set()) or set()
            if "POST" not in methods or path.startswith(("/api/", "/ui/static")):
                continue
            if path in self.NOT_WRITES:
                continue
            missing = [p for p in _path_params(path) if p not in self.PARAMS]
            self.assertEqual(
                [], missing,
                "write route {} has path parameter(s) {} with no test value - "
                "add them to EveryScreenTest.PARAMS".format(path, missing))
            posts.append(path)

        self.assertGreater(len(posts), 5, "write route discovery found almost nothing")

        client = client_for(app)
        async with client:
            await sign_in(client)
            for path in posts:
                url = path
                for name, value in self.PARAMS.items():
                    url = url.replace("{" + name + "}", value)
                response = await client.post(url, data=dict(self.WRITE_BODY))
                self.assertLess(
                    response.status_code, 500,
                    "{} returned {} - a write route must refuse in words, not "
                    "with a traceback:\n{}".format(
                        url, response.status_code, response.text[:600]))
                self.assertNotEqual(
                    422, response.status_code,
                    "{} returned 422, which usually means a literal path "
                    "segment is being parsed as a typed path parameter - check "
                    "route registration order".format(url))


def _path_params(path):
    import re

    return re.findall(r"\{([^}:]+)", path)
