"""An app wired to a resource group store (FR-WL-07).

Not a test module. `build` is the fixture the resource group API tests drive;
it lived beside the screen tests until the server-rendered console was deleted
(D-016), and those tests' subject - what reaches the operator - is now checked
against the JSON the console renders.
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

try:
    import httpx  # noqa: F401
    from fastapi import FastAPI  # noqa: F401

    WEB_DEPS = True
except ImportError:  # pragma: no cover - environment dependent
    WEB_DEPS = False

from tms.api.main import create_app  # noqa: E402
from tms.api.services import TmsService  # noqa: E402
from tms.collector.snapshot import (  # noqa: E402
    KIND_RESOURCE_GROUPS,
    InMemorySnapshotRepository,
    Snapshot,
    utcnow,
)
from tms.core.audit import AuditGuard, InMemoryAuditRepository  # noqa: E402
from tms.core.config import build_config  # noqa: E402
from tms.core.passwords import hash_password  # noqa: E402

# The row fixtures and the fake store live with the service-layer tests; there
# is one definition of what a resource_groups row looks like, not two.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_resource_group_config import (  # noqa: E402
    ADMIN,
    GLOBAL,
    SEL_ADMIN,
    SEL_CATCH_ALL,
    USER_LEAF,
    FakeStore,
)

USER = "op"
PASSWORD = "correct horse battery staple"


def build(store, workload_enabled=True, live=None):
    repository = InMemorySnapshotRepository()
    if live is not None:
        repository.save(Snapshot("prod-a", KIND_RESOURCE_GROUPS, utcnow(),
                                 payload={"groups": live}))
    config = build_config({
        "clusters": [{"name": "prod-a", "coordinator_url": "https://a.invalid:8443",
                      "expected_workers": 11, "node_environment": "cluster1"}],
        "trino": {"user": "tms-svc", "password": "pw"},
        "database": {"url": "postgresql://u:p@h:5432/d"},
        "collector": {"stale_threshold_seconds": 600},
        "workload": {"enabled": workload_enabled},
        "resource_groups": {"enabled": True},
        "portal": {
            "session_secret": "s" * 48,
            "local_users": {USER: {"password_hash": hash_password(PASSWORD, iterations=1000),
                                   "roles": ["admin"]}},
        },
    })
    audit = InMemoryAuditRepository()
    service = TmsService(
        config=config, repository=repository, audit_guard=AuditGuard(audit),
        audit_repository=audit, trino_clients={}, config_store=store)
    return create_app(config=config, service=service)


