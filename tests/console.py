"""Shared fixtures for driving the app through ASGI, with no infrastructure.

Not a test module - `build_service`, `client_for` and `sign_in` are imported by
most of the API tests. In-memory repositories and a stub Trino stand in, which
is enough to exercise routing, session handling, permissions, the write
ceremonies and the degraded paths. What it deliberately does not cover is real
SQL and real Trino behaviour - that stays in tests/integration/.

`WEB_DEPS` is False when fastapi/httpx are absent, so the suite still runs on a
bare interpreter.
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

try:
    import httpx
    from fastapi import FastAPI  # noqa: F401

    WEB_DEPS = True
except ImportError:  # pragma: no cover - environment dependent
    WEB_DEPS = False

from tms.api.main import create_app  # noqa: E402
from tms.api.services import TmsService  # noqa: E402
from tms.collector.snapshot import (  # noqa: E402
    KIND_HEALTH,
    KIND_QUERIES,
    KIND_RESOURCE_GROUPS,
    InMemorySnapshotRepository,
    Snapshot,
    utcnow,
)
from tms.core.audit import AuditGuard, InMemoryAuditRepository  # noqa: E402
from tms.core.config import build_config  # noqa: E402
from tms.core.passwords import hash_password  # noqa: E402
from tms.ops.repository import InMemorySequenceRepository  # noqa: E402
from tms.ops.sequence import RestartSequence  # noqa: E402
from tms.ops.service import RestartService  # noqa: E402

USER = "operator1"
PASSWORD = "Console-Test-9!"


class StubTrino:
    """Stands in for a coordinator. Records kills so the ceremony is testable."""

    def __init__(self):
        self.killed = []

    def kill_query(self, query_id, message):
        self.killed.append((query_id, message))

    def get_query(self, query_id):
        return {"queryId": query_id, "query": "SELECT 1", "state": "RUNNING"}


def build_service(roles=("admin",), with_data=True, workload=None,
                  clusters=("prod-a",)):
    repository = InMemorySnapshotRepository()
    now = utcnow()
    if with_data:
        repository.save(Snapshot("prod-a", KIND_QUERIES, now, payload={
            "summary": {"running": 1, "queued": 0, "long_running": 1, "total": 1},
            "queries": [{
                "query_id": "20260808_000000_00001_abcde", "state": "RUNNING",
                "user": "analyst", "source": "superset",
                "resource_group_id": ["global", "adhoc"],
                "elapsed_ms": 425000.0, "queued_ms": 12.0, "total_cpu_ms": 900.0,
                "peak_user_memory_bytes": 1048576, "physical_input_bytes": 2048,
                "progress_percentage": 42.0, "running_drivers": 3,
                "queued_drivers": 0, "fully_blocked": False,
                "query_preview": "SELECT count(*) FROM t", "query_truncated": False,
                "long_running": True,
            }],
        }))
        repository.save(Snapshot("prod-a", KIND_HEALTH, now, payload={
            "rollup_state": "CONCERNING", "rollup_enabled": True, "stale": False,
            "tests": [
                {"id": "H-01", "name": "Coordinator responsiveness", "state": "GOOD",
                 "observed_value": "responsive", "advice": ""},
                {"id": "H-05", "name": "Query failure rate (5m)", "state": "CONCERNING",
                 "observed_value": 7.5, "threshold": 20,
                 "advice": "7.5% of queries failed in the last 5 minutes."},
            ],
        }))

    config = build_config({
        # One cluster by default: most screens are per-cluster and a second one
        # would double every count the other tests assert on. The benchmark
        # screen is the exception - it is about comparing two - so it asks.
        "clusters": [{"name": name,
                      "coordinator_url": "https://{}.invalid:8443".format(name),
                      "expected_workers": 12,
                      "trino_ui_url": "https://{}.invalid:8443/ui/".format(name)}
                     for name in clusters],
        "trino": {"user": "tms-svc", "password": "pw"},
        "database": {"url": "postgresql://u:p@h:5432/d"},
        "collector": {"stale_threshold_seconds": 600},
        "deeplinks": {"superset_url": "https://superset.invalid/"},
        "workload": workload or {},
        "portal": {
            "session_secret": "s" * 48,
            "local_users": {USER: {"password_hash": hash_password(PASSWORD, iterations=1000),
                                   "roles": list(roles)}},
        },
    })
    trino = StubTrino()
    # One repository, not two - the guard writes and the service reads through
    # the same object, exactly as in production.
    audit = InMemoryAuditRepository()
    service = TmsService(
        config=config, repository=repository, audit_guard=AuditGuard(audit),
        audit_repository=audit, trino_clients={"prod-a": trino},
    )
    return config, service, trino


def client_for(app):
    """ASGITransport is async-only in httpx 0.28, so every request is awaited."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://tms.test", follow_redirects=False,
    )


async def sign_in(client, password=PASSWORD):
    """Sign in the way the console does: JSON to the API, cookie back.

    The form POST this used to make belonged to the server-rendered console,
    which was deleted once the React one covered all twelve screens (D-016).
    """
    return await client.post(
        "/api/v1/login", json={"username": USER, "password": password})


class _StubGateway:
    """Records activation changes the way the real client applies them."""

    def __init__(self):
        self.calls = []

    def set_active(self, name, active):
        self.calls.append((name, active))


class _StubExecutor:
    """A manual executor. The automated one has its own tests."""

    name = "stub"
    automated = False

    def start(self, cluster, sequence_id):
        from tms.ops.executor import PENDING_OPERATOR

        return PENDING_OPERATOR

    def status(self, cluster, sequence_id):
        from tms.ops.executor import PENDING_OPERATOR

        return PENDING_OPERATOR

    def describe(self, cluster):
        return {"automated": False,
                "title": "I will restart {} myself".format(cluster),
                "instructions": "Do the thing.",
                "waiting": "Waiting on the stub."}



def fully_wired_app():
    """An app with every integration switched ON.

    Written after `/gateway` returned 500 for every request while 563 tests
    passed: every test until then built an app with Gateway, workload, fleet
    and restarts *disabled*, so each of those services returned early and never
    reached the code that was broken.

    Lives here rather than in one test module because the API sweep walks every
    route against it - a route whose feature is off answers 503 and proves
    nothing.
    """
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
        snapshots=service.repository, gateway_client=_StubGateway(),
        audit_guard=service.audit, executor=_StubExecutor())
    # One sequence so /restarts/{id} has something to render.
    restarts.repository.create(
        RestartSequence("prod-a", "rendering test", "syhcho"))

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
