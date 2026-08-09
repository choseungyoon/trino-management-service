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
from tms.ops.executor import PENDING_OPERATOR, RUNNING, SUCCEEDED  # noqa: E402
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
                "instructions": "Do the thing."}


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
