"""A self-contained TMS server for browser tests.

No PostgreSQL, no Trino, no load: in-memory repositories hold pre-seeded
snapshots and a stub client absorbs writes. Starting this costs nothing, which
matters because an earlier version of the local demo drove real tpch queries
and pinned six CPU cores.

Serves over HTTPS with a throwaway certificate generated at import time. That
is not incidental - the session cookie is `Secure`, so a browser will refuse to
store it over plain HTTP and every test would fail at the login step.

Usage:
    from tests.browser.harness import serve
    with serve() as base_url:
        ...

Python 3.9 compatible.
"""

import contextlib
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "src",
    ),
)

from tms.api.main import create_app  # noqa: E402
from tms.api.services import TmsService  # noqa: E402
from tms.collector.snapshot import (  # noqa: E402
    KIND_HEALTH,
    KIND_FLEET,
    KIND_QUERIES,
    KIND_RESOURCE_GROUPS,
    InMemorySnapshotRepository,
    Snapshot,
    utcnow,
)
from tms.core.audit import AuditGuard, InMemoryAuditRepository  # noqa: E402
from tms.core.config import build_config  # noqa: E402
from tests.browser.rgstore import InMemoryResourceGroupStore
from tms.core.passwords import hash_password  # noqa: E402

USER = "operator1"
PASSWORD = "Browser-Test-9!"
CLUSTERS = ("prod-a", "prod-b")


class StubTrino:
    def __init__(self):
        self.killed = []

    def kill_query(self, query_id, message):
        self.killed.append((query_id, message))

    def get_query(self, query_id):
        return {"queryId": query_id, "query": "SELECT 1", "state": "RUNNING"}


def _query(qid, user, source, elapsed_ms, long_running=False, state="RUNNING"):
    return {
        "query_id": qid, "state": state, "user": user, "source": source,
        "resource_group_id": ["global", "adhoc"],
        "elapsed_ms": elapsed_ms, "queued_ms": 15.0, "total_cpu_ms": elapsed_ms * 2,
        "peak_user_memory_bytes": 402653184, "physical_input_bytes": 10485760,
        "progress_percentage": 61.0, "running_drivers": 4, "queued_drivers": 0,
        "fully_blocked": False,
        "query_preview": "SELECT o.orderstatus, count(*) FROM orders o GROUP BY 1",
        "query_truncated": False, "long_running": long_running,
    }


def build_app(workload_enabled=False, seed=None, gateway=None,
              resource_groups=False, password=None, session_secret=None,
              fleet_jobs=False):
    repository = InMemorySnapshotRepository()
    now = utcnow()

    repository.save(Snapshot("prod-a", KIND_QUERIES, now, payload={
        "summary": {"running": 2, "queued": 1, "long_running": 1, "total": 3},
        "queries": [
            _query("20260808_000000_00001_aaaaa", "analyst", "superset", 425000.0, True),
            _query("20260808_000000_00002_bbbbb", "dbt_runner", "dbt", 12000.0),
            _query("20260808_000000_00003_ccccc", "analyst", "tableau", 800.0,
                   state="QUEUED"),
        ],
    }))
    repository.save(Snapshot("prod-a", KIND_RESOURCE_GROUPS, now, payload={
        "groups": [
            {"id": "global", "path": ["global"], "name": "global", "depth": 0,
             "running": 3, "queued": 1, "bottleneck": None},
            {"id": "legacy.batch", "path": ["legacy", "batch"], "name": "batch",
             "depth": 1, "running": 2, "queued": 0, "bottleneck": None},
        ],
        "tree": [], "summary": {}, "complete": False,
    }))
    repository.save(Snapshot("prod-a", KIND_HEALTH, now, payload={
        "rollup_state": "CONCERNING", "rollup_enabled": True, "stale": False,
        "tests": [
            {"id": "H-01", "name": "Coordinator responsiveness", "state": "GOOD",
             "observed_value": "responsive", "advice": ""},
            {"id": "H-03", "name": "Worker registration", "state": "GOOD",
             "observed_value": {"active_workers": 12, "expected_workers": 12,
                                "planned_out": 0, "unplanned_missing": 0},
             "advice": ""},
            {"id": "H-05", "name": "Query failure rate (5m)", "state": "CONCERNING",
             "observed_value": 7.5, "threshold": 20,
             "advice": "7.5% of queries failed in the last 5 minutes. Read this "
                       "with H-06 to tell user SQL errors from engine problems."},
        ],
    }))

    # prod-b is deliberately degraded so the UNKNOWN treatment is on screen.
    repository.save(Snapshot("prod-b", KIND_HEALTH, now, payload={
        "rollup_state": "BAD", "rollup_enabled": True, "stale": False,
        "tests": [
            {"id": "H-01", "name": "Coordinator responsiveness", "state": "BAD",
             "observed_value": "unreachable",
             "advice": "The coordinator could not be reached."},
        ],
    }))

    config = build_config({
        "clusters": [
            {"name": "prod-a", "coordinator_url": "https://a.invalid:8443",
             "expected_workers": 11, "trino_ui_url": "https://a.invalid:8443/ui/",
             "node_environment": "cluster1"},
            {"name": "prod-b", "coordinator_url": "https://b.invalid:8443",
             "expected_workers": 11, "node_environment": "cluster2"},
        ],
        "trino": {"user": "tms-svc", "password": "pw"},
        "database": {"url": "postgresql://u:p@h:5432/d"},
        "collector": {"query_poll_interval_seconds": 5, "stale_threshold_seconds": 600},
        "deeplinks": {"superset_url": "https://superset.invalid/"},
        "workload": {"enabled": workload_enabled},
        "gateway": gateway or {},
        "resource_groups": {"enabled": bool(resource_groups)},
        "portal": {
            # Overridable so a hosted demo does not run on a password that is
            # sitting in a public repository. Local runs keep the fixed pair -
            # a throwaway credential the browser tests can type.
            "session_secret": session_secret or ("b" * 48),
            "local_users": {
                USER: {"password_hash": hash_password(password or PASSWORD,
                                                      iterations=1000),
                       "roles": ["admin"]}},
        },
    })
    for snapshot in seed or []:
        repository.save(snapshot)

    if fleet_jobs:
        # The fleet screen needs an inventory snapshot before its job panel has
        # anywhere to live.
        repository.save(Snapshot("prod-a", KIND_FLEET, now, payload={
            "nodes": [
                {"host": "trino-a-w1", "address": "10.0.0.11", "role": "worker",
                 "cluster": "prod-a", "reachable": True, "state": "ACTIVE",
                 "version": "477", "environment": "cluster1", "uptime": "6d",
                 "coordinator": False, "error": None},
                {"host": "trino-a-c1", "address": "10.0.0.10", "role": "coordinator",
                 "cluster": "prod-a", "reachable": True, "state": "ACTIVE",
                 "version": "477", "environment": "cluster1", "uptime": "6d",
                 "coordinator": True, "error": None},
            ],
            "summary": {"total": 2, "reachable": 2, "unreachable": 0,
                        "workers": 1, "shutting_down": 0},
            "notes": [], "node_counts": {"ActiveNodeCount": 2}, "inventory_size": 2,
        }))

    trino = StubTrino()
    audit = InMemoryAuditRepository()
    service = TmsService(
        config=config, repository=repository, audit_guard=AuditGuard(audit),
        audit_repository=audit, trino_clients={name: trino for name in CLUSTERS},
        config_store=InMemoryResourceGroupStore() if resource_groups else None,
    )
    fleet = None
    if fleet_jobs:
        from tms.fleet.jobs import JobRunner, build_jobs
        from tms.fleet.jobstore import InMemoryJobRepository
        from tms.fleet.service import FleetService

        definitions = build_jobs({
            "scale_out": {
                "playbook": __file__, "title": "Add workers",
                "description": "Provisions worker VMs and joins them to the cluster.",
                "parameters": {"count": {"label": "Workers to add", "min": 1,
                                         "max": 4, "default": 2}},
            },
        })
        job_repository = InMemoryJobRepository()
        past = job_repository.create("prod-a", "scale_out", "sre.kim", ["admin"],
                                     "month-end reporting load", {"count": 2})
        for line in ("PLAY [add workers] " + "*" * 40,
                     "TASK [provision : create VM] " + "*" * 32,
                     "changed: [trino-a-w12]",
                     "changed: [trino-a-w13]",
                     "TASK [trino : join cluster] " + "*" * 34,
                     "ok: [trino-a-w12]",
                     "ok: [trino-a-w13]",
                     "PLAY RECAP " + "*" * 49,
                     "trino-a-w12 : ok=7  changed=4  unreachable=0  failed=0",
                     "trino-a-w13 : ok=7  changed=4  unreachable=0  failed=0"):
            job_repository.append_output(past["id"], line)
        job_repository.finish(past["id"], "SUCCEEDED", exit_code=0)

        def demo_runner(command, timeout, on_line, env=None, cwd=None):
            for line in ("PLAY [add workers] " + "*" * 40,
                         "TASK [provision : create VM] " + "*" * 32):
                on_line(line)
            return {"rc": 0}

        fleet = FleetService(
            config=config, snapshots=repository, audit_guard=AuditGuard(audit),
            transport_factory=lambda: None,
            job_runner=JobRunner(jobs=definitions,
                                 cluster_inventories={"prod-a": __file__},
                                 runner=demo_runner),
            job_repository=job_repository)

    # The work board, seeded from the documents and given the kind of activity
    # a real board has after a week - a comment thread and one item that moved.
    from tms.work.items import IN_PROGRESS
    from tms.work.seed import seed as seed_board
    from tms.work.service import BoardService
    from tms.work.store import InMemoryBoardRepository

    board_repository = InMemoryBoardRepository()
    seed_board(board_repository)
    board_repository.create(key="REQ-1", kind="request",
                            title="쿼리를 id 말고 사용자로도 죽일 수 있으면 좋겠다",
                            status="planned", created_by="sre.kim",
                            body="지금은 사용자 한 명의 쿼리 여덟 개를 하나씩 죽여야 한다.")
    board_repository.add_comment(
        "REQ-1", "syhcho",
        "여러 건을 한 번에 죽이는 것은 절대규칙 3 의 확인 절차와 맞물린다.\n"
        "reason 을 한 번 받고 여덟 건을 죽이는 게 맞는지부터 정해야 한다.")
    board_repository.add_comment("D-2", "sre.kim",
                                 "SSH 범위 때문에 아직 결정 못 했다.")
    board_repository.update("FR-BM-04", "syhcho", status=IN_PROGRESS)

    return create_app(config=config, service=service, fleet=fleet,
                      board=BoardService(board_repository)), trino


def _make_cert(directory):
    """Self-signed cert for 127.0.0.1. The Secure cookie requires HTTPS."""
    key = os.path.join(directory, "server.key")
    crt = os.path.join(directory, "server.crt")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", key, "-out", crt, "-days", "1", "-subj", "/CN=localhost",
         "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
        check=True, capture_output=True,
    )
    return key, crt


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextlib.contextmanager
def serve(workload_enabled=False, seed=None, gateway=None,
          resource_groups=False, fleet_jobs=False):
    """Run the console on a free port. Yields (base_url, stub_trino)."""
    import uvicorn

    app, trino = build_app(workload_enabled=workload_enabled, seed=seed,
                           resource_groups=resource_groups,
                           fleet_jobs=fleet_jobs, gateway=gateway)
    port = _free_port()
    with tempfile.TemporaryDirectory() as tmp:
        key, crt = _make_cert(tmp)
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error",
                                ssl_keyfile=key, ssl_certfile=crt)
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        base = "https://127.0.0.1:{}".format(port)
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        deadline = time.time() + 30
        while time.time() < deadline:
            if getattr(server, "started", False):
                break
            time.sleep(0.1)
        else:  # pragma: no cover
            raise RuntimeError("server did not start")

        try:
            yield base, trino
        finally:
            server.should_exit = True
            thread.join(timeout=10)


def sign_in(page, base):
    page.goto(base + "/login")
    page.fill('input[name="username"]', USER)
    page.fill('input[name="password"]', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_url(lambda url: "/login" not in url, timeout=10000)
