"""The safe restart sequence over HTTP (FR-CO-02).

The state machine and the service have their own tests. What is checked here
is the wire: that each step is reachable only through its own POST, that the
payload says what the sequence is waiting for, that the log carries the
playbook's output, and - most importantly - that no route offers a way around
the order CLAUDE.md rule 5 fixes.

These were screen tests until the server-rendered console was deleted (D-016).
The assertions moved from rendered phrases to the payload the console renders;
what is being protected did not change.

The Gateway and the executor are stubs. The transports are covered in
tests/test_gateway_client.py and tests/test_ansible_executor.py.
"""

import os
import sys
import unittest
from contextlib import asynccontextmanager

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))
# `console` builds a fully wired service; importing it beats a second copy
# that would drift from the one every other API test uses.
sys.path.insert(0, _HERE)

try:
    import httpx  # noqa: F401
    from fastapi import FastAPI  # noqa: F401

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

from console import build_service, client_for, sign_in  # noqa: E402


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




@unittest.skipUnless(WEB_DEPS, "fastapi/httpx not installed")
class RestartApiTest(unittest.IsolatedAsyncioTestCase):
    @asynccontextmanager
    async def session(self, **kwargs):
        """A signed-in admin against a freshly wired app."""
        app, restarts, gateway = build_app(**kwargs)
        async with client_for(app) as client:
            await sign_in(client)
            yield client, restarts, gateway

    async def _start(self, client, reason="applying CHG-4471"):
        return await client.post("/api/v1/clusters/prod-a/restarts",
                                 json={"reason": reason})

    @staticmethod
    async def _sequence_id(client):
        overview = (await client.get("/api/v1/restarts")).json()
        return overview["active"][0]["id"]

    @staticmethod
    async def _get(client, sequence_id):
        return (await client.get("/api/v1/restarts/{}".format(sequence_id))).json()

    @staticmethod
    def _log(payload):
        return " ".join(line["message"] for line in payload["history"])

    # --------------------------------------------------------- before it runs

    async def test_the_whole_sequence_is_published_before_it_begins(self):
        """Someone about to take a cluster out of rotation should see what TMS
        will do before they commit, not discover it a button at a time.

        ⛔ From the server, so the console cannot show a procedure the code no
        longer follows.
        """
        async with self.session() as (client, _r, _g):
            preview = (await client.get("/api/v1/restarts")).json()["preview"]
        labels = " ".join(step["label"] for step in preview)
        self.assertEqual(6, len(preview))
        self.assertEqual([1, 2, 3, 4, 5, 6], [step["number"] for step in preview])
        for phrase in ("Stop new queries reaching it", "Wait for every running query",
                       "Verify health is GOOD", "Put it back in rotation"):
            self.assertIn(phrase, labels)

    async def test_a_reason_is_required_to_begin(self):
        async with self.session() as (client, restarts, gateway):
            response = await self._start(client, reason="   ")
        self.assertEqual(400, response.status_code)
        self.assertEqual([], gateway.calls, "traffic was not touched")
        self.assertEqual([], restarts.active())

    async def test_beginning_stops_traffic_and_reports_what_it_waits_for(self):
        async with self.session() as (client, restarts, gateway):
            response = await self._start(client)
            self.assertEqual(201, response.status_code, response.text[:300])
            payload = await self._get(client, response.json()["id"])

        self.assertEqual([("trino-prod-a-1", False)], gateway.calls,
                         "the backend is chosen by URL match, not by name")
        self.assertTrue(payload["traffic_stopped"])
        self.assertIn("Waiting for 1 running query to finish.", self._log(payload))

    async def test_a_non_admin_cannot_begin_one(self):
        async with self.session(roles=("viewer",)) as (client, _r, gateway):
            response = await self._start(client)
        self.assertEqual(403, response.status_code)
        self.assertEqual([], gateway.calls)

    async def test_a_viewer_can_watch_but_cannot_advance(self):
        """A restart in progress is something everyone needs to see; advancing
        it is not something everyone may do."""
        async with self.session(roles=("viewer",)) as (client, restarts, _g):
            # Started by someone else - a viewer has no route to begin one.
            stored = restarts.repository.create(
                _sequence("prod-a", "applying CHG-4471", "admin1"))
            readable = await client.get("/api/v1/restarts/{}".format(stored.id))
            refused = await client.post(
                "/api/v1/restarts/{}/abort".format(stored.id), json={"note": "no"})

        self.assertEqual(200, readable.status_code)
        self.assertEqual("admin1", readable.json()["actor"])
        self.assertEqual(403, refused.status_code)

    # -------------------------------------------------------------- the order

    async def test_the_restart_step_is_refused_while_queries_are_running(self):
        """The gate that actually prevents the incident."""
        async with self.session(running=3) as (client, restarts, _g):
            await self._start(client)
            sequence_id = await self._sequence_id(client)
            response = await client.post(
                "/api/v1/restarts/{}/restart".format(sequence_id))

        self.assertEqual(400, response.status_code, response.text[:300])
        self.assertEqual("DRAINING",
                         restarts.repository.load(sequence_id).sequence.state)
        # The refusal counts them. "Not yet" without a number is not something
        # an operator can act on.
        self.assertIn("3 queries are still running", response.text)

    async def test_traffic_is_not_restored_until_health_is_good(self):
        async with self.session(running=0, health="BAD") as (client, restarts, gateway):
            await self._start(client)
            sequence_id = await self._sequence_id(client)
            await client.post("/api/v1/restarts/{}/restart".format(sequence_id))
            await client.post("/api/v1/restarts/{}/restarted".format(sequence_id))
            refused = await client.post(
                "/api/v1/restarts/{}/complete".format(sequence_id))

        self.assertEqual(400, refused.status_code, refused.text[:300])
        self.assertEqual("VERIFYING",
                         restarts.repository.load(sequence_id).sequence.state)
        self.assertEqual([("trino-prod-a-1", False)], gateway.calls,
                         "the backend was never reactivated")

    async def test_the_whole_sequence_completes_and_restores_traffic(self):
        async with self.session(running=0, health="GOOD") as (client, restarts, gateway):
            await self._start(client)
            sequence_id = await self._sequence_id(client)
            await client.post("/api/v1/restarts/{}/restart".format(sequence_id))
            await client.post("/api/v1/restarts/{}/restarted".format(sequence_id))
            await client.post("/api/v1/restarts/{}/complete".format(sequence_id))
            payload = await self._get(client, sequence_id)

        self.assertEqual([("trino-prod-a-1", False), ("trino-prod-a-1", True)],
                         gateway.calls)
        self.assertEqual([], restarts.active(), "the sequence is finished")
        self.assertEqual("COMPLETED", payload["state"])
        self.assertTrue(payload["is_terminal"])
        self.assertFalse(payload["traffic_stopped"])

    async def test_forcing_past_the_drain_needs_its_own_reason(self):
        async with self.session(running=2) as (client, restarts, _g):
            await self._start(client)
            sequence_id = await self._sequence_id(client)
            blank = await client.post(
                "/api/v1/restarts/{}/force-drain".format(sequence_id),
                json={"reason": ""})
            self.assertNotEqual(200, blank.status_code)
            self.assertEqual("DRAINING",
                             restarts.repository.load(sequence_id).sequence.state)

            await client.post("/api/v1/restarts/{}/force-drain".format(sequence_id),
                              json={"reason": "query stuck for 40 minutes"})
            payload = await self._get(client, sequence_id)

        self.assertEqual("DRAINED", payload["state"])
        self.assertIn("FORCED past the drain with 2 queries", self._log(payload))

    async def test_aborting_restores_traffic_rather_than_just_stopping(self):
        """An abandoned sequence leaves a cluster receiving nothing - a quiet
        outage, because every other cluster is green."""
        async with self.session() as (client, restarts, gateway):
            await self._start(client)
            sequence_id = await self._sequence_id(client)
            await client.post("/api/v1/restarts/{}/abort".format(sequence_id),
                              json={"note": "change called off"})

        self.assertEqual([("trino-prod-a-1", False), ("trino-prod-a-1", True)],
                         gateway.calls)
        self.assertEqual("ABORTED",
                         restarts.repository.load(sequence_id).sequence.state)

    async def test_a_failed_reactivation_keeps_the_sequence_active(self):
        """Still holding traffic back, so it must not disappear from the UI.

        `active` is what the console follows around: a cluster out of rotation
        is invisible on every other screen, and the banner is built from this.
        """
        gateway = StubGateway(fail_on=1)
        async with self.session(gateway=gateway) as (client, restarts, _g):
            await self._start(client)
            sequence_id = await self._sequence_id(client)
            failed = await client.post(
                "/api/v1/restarts/{}/abort".format(sequence_id),
                json={"note": "change called off"})
            overview = (await client.get("/api/v1/restarts")).json()

        # ⛔ 503 with the sentence that names the fix, not a 500 with a
        # traceback. The cluster is still receiving nothing.
        self.assertEqual(503, failed.status_code)
        self.assertIn("reactivate it in the Gateway", failed.text)

        self.assertEqual("ABORTING",
                         restarts.repository.load(sequence_id).sequence.state)
        self.assertEqual(["prod-a"], [s["cluster"] for s in overview["active"]])

    async def test_a_finished_sequence_records_when_as_well_as_who(self):
        """"Who and why" without "when" is not a record anyone can use."""
        async with self.session(running=0, health="GOOD") as (client, restarts, _g):
            await self._start(client)
            sequence_id = await self._sequence_id(client)
            await client.post("/api/v1/restarts/{}/restart".format(sequence_id))
            await client.post("/api/v1/restarts/{}/restarted".format(sequence_id))
            await client.post("/api/v1/restarts/{}/complete".format(sequence_id))
            recent = (await client.get("/api/v1/restarts")).json()["recent"]

        row = next(r for r in recent if r["id"] == sequence_id)
        self.assertIsNotNone(row["started_at"])
        self.assertIsNotNone(row["finished_at"],
                             "a finished sequence records when")

    # ------------------------------------------------------- who is restarting

    async def test_manual_mode_says_it_is_the_operator_s_turn(self):
        """The first real run stalled here: the console showed only an "it is
        back up" button, so the operator pressed "restart", saw nothing happen,
        and reasonably concluded TMS had failed."""
        async with self.session(running=0, executor=ManualExecutor()) \
                as (client, restarts, _g):
            await self._start(client)
            sequence_id = await self._sequence_id(client)

            drained = await self._get(client, sequence_id)
            # ⛔ First person. "Restart X now" reads as an instruction to TMS.
            self.assertFalse(drained["automated"])
            self.assertIn("I will restart prod-a myself", drained["executor"]["title"])

            await client.post("/api/v1/restarts/{}/restart".format(sequence_id))
            restarting = await self._get(client, sequence_id)

        self.assertIn("Restart prod-a now, using your normal procedure",
                      restarting["executor"]["waiting"])
        # And the log says the same thing rather than "waiting for the
        # operator", which was read as TMS working.
        self.assertIn("TMS is NOT restarting prod-a", self._log(restarting))

    async def test_an_automated_restart_does_not_ask_the_operator_to_act(self):
        """The mirror image: with Ansible driving it, telling the operator to
        go and restart the cluster by hand would cause a double restart."""
        async with self.session(running=0, executor=StubExecutor(automated=True)) \
                as (client, restarts, _g):
            await self._start(client)
            sequence_id = await self._sequence_id(client)
            await client.post("/api/v1/restarts/{}/restart".format(sequence_id))
            payload = await self._get(client, sequence_id)

        self.assertTrue(payload["automated"])
        self.assertEqual("running", payload["executor_state"])

    async def test_a_failed_playbook_stops_claiming_it_is_running(self):
        """Reported from the first ansible run: the log showed
        `[Errno 2] No such file or directory: 'ansible-playbook'` while the
        panel beside it still said "The playbook is running", so the operator
        had no reason to think anything needed doing."""
        from tms.ops.executor import FAILED

        executor = StubExecutor(automated=True, state=FAILED)
        async with self.session(running=0, executor=executor) as (client, restarts, _g):
            await self._start(client)
            sequence_id = await self._sequence_id(client)
            await client.post("/api/v1/restarts/{}/restart".format(sequence_id))
            payload = await self._get(client, sequence_id)

        # The console branches on this. "running" here is what made it keep
        # saying the playbook was running after it had stopped.
        self.assertEqual("failed", payload["executor_state"])

    async def test_the_failure_advice_does_not_name_a_missing_control(self):
        """`restart` is refused from RESTARTING. The log line used to say "run
        the restart again", which is a control that is not on the screen."""
        from tms.ops.executor import FAILED

        async with self.session(running=0,
                                executor=StubExecutor(automated=True, state=FAILED)) \
                as (client, restarts, _g):
            await self._start(client)
            sequence_id = await self._sequence_id(client)
            await client.post("/api/v1/restarts/{}/restart".format(sequence_id))
            # The failure is noticed on the next observation, which is what the
            # live view does every couple of seconds.
            await self._get(client, sequence_id)
            again = await client.post(
                "/api/v1/restarts/{}/restart".format(sequence_id))
            history = restarts.repository.load(sequence_id).sequence.history

        self.assertEqual(400, again.status_code, "the retry really is refused")
        failure = [h["message"] for h in history if h["level"] == "error"]
        self.assertTrue(failure)
        self.assertNotIn("run the restart again", " ".join(failure))
        self.assertIn("Abort to put it back", " ".join(failure))

    # ----------------------------------------------------------- the live view

    async def test_playbook_output_reaches_the_log_marked_as_output(self):
        executor = StubExecutor(lines=["PLAY [restart trino] ***",
                                       "TASK [stop worker 1] ***"])
        async with self.session(running=0, executor=executor) as (client, restarts, _g):
            await self._start(client)
            sequence_id = await self._sequence_id(client)
            await client.post("/api/v1/restarts/{}/restart".format(sequence_id))
            payload = await self._get(client, sequence_id)

        lines = {line["message"]: line["level"] for line in payload["history"]}
        self.assertIn("TASK [stop worker 1] ***", lines)
        self.assertEqual("output", lines["TASK [stop worker 1] ***"],
                         "verbatim playbook output is not TMS prose")

    async def test_output_is_not_recorded_twice_when_the_view_is_polled(self):
        """The live view reloads the sequence on every poll; an in-memory
        cursor would restart at zero and duplicate every line."""
        executor = StubExecutor(lines=["PLAY [restart trino] ***"])
        async with self.session(running=0, executor=executor) as (client, restarts, _g):
            await self._start(client)
            sequence_id = await self._sequence_id(client)
            await client.post("/api/v1/restarts/{}/restart".format(sequence_id))
            for _ in range(4):
                await self._get(client, sequence_id)
            history = restarts.repository.load(sequence_id).sequence.history

        occurrences = [h for h in history
                       if h["message"] == "PLAY [restart trino] ***"]
        self.assertEqual(1, len(occurrences), history)

    async def test_a_finished_playbook_advances_the_sequence_by_itself(self):
        executor = StubExecutor(lines=["PLAY RECAP ***"], state=SUCCEEDED)
        async with self.session(running=0, executor=executor) as (client, restarts, _g):
            await self._start(client)
            sequence_id = await self._sequence_id(client)
            await client.post("/api/v1/restarts/{}/restart".format(sequence_id))
            await self._get(client, sequence_id)

        self.assertEqual("VERIFYING",
                         restarts.repository.load(sequence_id).sequence.state)

    async def test_a_repeated_step_post_cannot_replay_it(self):
        """A stale tab must not fire step 4 at a cluster that has moved on."""
        async with self.session(running=0) as (client, restarts, _g):
            await self._start(client)
            sequence_id = await self._sequence_id(client)
            await client.post("/api/v1/restarts/{}/restart".format(sequence_id))
            await client.post("/api/v1/restarts/{}/restart".format(sequence_id))
            stored = restarts.repository.load(sequence_id)

        self.assertEqual("RESTARTING", stored.sequence.state)

    async def test_a_second_restart_of_the_same_cluster_is_refused(self):
        async with self.session() as (client, _r, gateway):
            await self._start(client)
            response = await self._start(client, reason="another change")
        self.assertNotEqual(201, response.status_code)
        self.assertEqual(1, len(gateway.calls), "traffic was stopped once")

    # ------------------------------------------------------- when it is off

    async def test_without_a_gateway_every_route_says_so(self):
        """⛔ 503, not 404. A feature that is switched off and one that does not
        exist look identical to a client otherwise."""
        config, service, _trino = build_service()
        app = create_app(config=config, service=service, restarts=None)
        async with client_for(app) as client:
            await sign_in(client)
            listed = await client.get("/api/v1/restarts")
            attempted = await self._start(client)

        self.assertEqual(503, listed.status_code)
        self.assertEqual(503, attempted.status_code)


if __name__ == "__main__":
    unittest.main()
