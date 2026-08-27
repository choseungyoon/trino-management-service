"""Deploying a catalog: the gate, the secret, and what a deploy does not do.

⛔ The property that matters most here is what is *absent*: nothing in this
service restarts anything. Trino reads static catalogs only at startup, so a
deploy leaves a file on the nodes and nothing using it - and restarting belongs
to the safe sequence, which drains first (absolute rule 5).
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from tms.api.errors import Forbidden, InvalidRequest  # noqa: E402
from tms.api.permissions import Principal  # noqa: E402
from tms.core.audit import AuditGuard, InMemoryAuditRepository  # noqa: E402
from tms.core.config import build_config  # noqa: E402
from tms.ops.catalogservice import CatalogService  # noqa: E402
from tms.ops.catalogstore import InMemoryCatalogRepository  # noqa: E402

ADMIN = Principal("sre.kim", ["admin"])
VIEWER = Principal("reader", ["viewer"])


def build(runner=None, development=("dev-a",)):
    config = build_config({
        "clusters": [{"name": n, "coordinator_url": "https://{}.invalid:8443".format(n),
                      "expected_workers": 2} for n in ("dev-a", "prod-a")],
        "trino": {"user": "tms-svc", "password": "pw"},
        "database": {"url": "postgresql://u:p@h:5432/d"},
    })
    audit = InMemoryAuditRepository()
    service = CatalogService(
        config=config, repository=InMemoryCatalogRepository(),
        audit_guard=AuditGuard(audit),
        playbook="/etc/tms/deploy-catalog.yml",
        inventories={"dev-a": "/etc/tms/dev-a.ini", "prod-a": "/etc/tms/prod-a.ini"},
        development_clusters=list(development),
        runner=runner or (lambda command, timeout, on_line: {"rc": 0}))
    return service, audit


def wait(service, cluster):
    import time

    for _ in range(300):
        if not service.is_busy(cluster):
            return
        time.sleep(0.01)
    raise AssertionError("the deployment never finished")


def a_draft(service, name="pg_reporting", properties=None):
    return service.create(
        ADMIN, name=name, connector="postgresql",
        properties=properties if properties is not None else {
            "connection-url": "jdbc:postgresql://db/x",
            "connection-password": "${ENV:PG_PASSWORD}"},
        reason="reporting needs it")


class NoRestartTest(unittest.TestCase):
    """⛔ The absence being protected."""

    def test_the_command_writes_a_file_and_says_nothing_about_restarting(self):
        seen = {}

        def runner(command, timeout, on_line):
            seen["command"] = command
            return {"rc": 0}

        service, _audit = build(runner=runner)
        draft = a_draft(service)
        service.deploy(ADMIN, draft["id"], "dev-a", reason="first try")
        wait(service, "dev-a")

        joined = " ".join(seen["command"])
        self.assertIn("catalog_action=deploy", joined)
        self.assertNotIn("restart", joined.lower())
        # The inventory is chosen by name; the cluster never becomes an argument.
        self.assertIn("/etc/tms/dev-a.ini", seen["command"])


class SecretTest(unittest.TestCase):
    def test_a_plaintext_credential_never_reaches_a_draft(self):
        service, _audit = build()
        with self.assertRaises(InvalidRequest) as caught:
            service.create(ADMIN, name="c", connector="postgresql",
                           properties={"connection-password": "hunter2"},
                           reason="why")
        self.assertIn("${ENV:", str(caught.exception))

    def test_the_variables_a_deploy_needs_are_reported(self):
        """⛔ A reference whose variable is missing on the node stops the
        server booting. Saying which ones beforehand is the whole point."""
        service, _audit = build()
        draft = a_draft(service)
        self.assertEqual(["PG_PASSWORD"], draft["environment"])


class DevelopmentGateTest(unittest.TestCase):
    def test_an_unproved_catalog_is_refused_on_production(self):
        service, _audit = build()
        draft = a_draft(service)
        with self.assertRaises(InvalidRequest) as caught:
            service.deploy(ADMIN, draft["id"], "prod-a", reason="ship it")
        self.assertIn("dev-a", str(caught.exception))

    def test_a_successful_development_deploy_unlocks_production(self):
        service, _audit = build()
        draft = a_draft(service)
        service.deploy(ADMIN, draft["id"], "dev-a", reason="first try")
        wait(service, "dev-a")

        after = service.repository.get(draft["id"])
        self.assertEqual("dev-a", after["verified_on"])
        service.deploy(ADMIN, draft["id"], "prod-a", reason="ship it")
        wait(service, "prod-a")

    def test_a_failed_development_deploy_does_not_unlock_production(self):
        service, _audit = build(
            runner=lambda c, t, on_line: {"rc": 2, "error": "unreachable"})
        draft = a_draft(service)
        service.deploy(ADMIN, draft["id"], "dev-a", reason="first try")
        wait(service, "dev-a")

        self.assertIsNone(service.repository.get(draft["id"])["verified_on"])
        with self.assertRaises(InvalidRequest):
            service.deploy(ADMIN, draft["id"], "prod-a", reason="ship it")

    def test_editing_after_proving_sends_it_back_to_development(self):
        """⛔ Otherwise somebody proves a working catalog, changes a property,
        and ships the change on a test that never saw it."""
        service, _audit = build()
        draft = a_draft(service)
        service.deploy(ADMIN, draft["id"], "dev-a", reason="first try")
        wait(service, "dev-a")

        service.save(ADMIN, draft["id"], connector="postgresql",
                     properties={"connection-url": "jdbc:postgresql://db/OTHER"},
                     reason="wrong database")
        self.assertIsNone(service.repository.get(draft["id"])["verified_on"])
        with self.assertRaises(InvalidRequest):
            service.deploy(ADMIN, draft["id"], "prod-a", reason="ship it")

    def test_a_cosmetic_edit_keeps_the_proof(self):
        """Notes are not the catalog. Re-proving for a comment would teach
        people to skip the gate."""
        service, _audit = build()
        draft = a_draft(service)
        service.deploy(ADMIN, draft["id"], "dev-a", reason="first try")
        wait(service, "dev-a")

        service.save(ADMIN, draft["id"], connector="postgresql",
                     properties=draft["properties"], notes="owned by BI",
                     reason="noting the owner")
        self.assertEqual("dev-a", service.repository.get(draft["id"])["verified_on"])

    def test_removing_needs_no_proof(self):
        """Taking a catalog off a cluster cannot be validated by proving it
        somewhere else, and refusing to remove one is its own hazard."""
        service, _audit = build()
        draft = a_draft(service)
        service.deploy(ADMIN, draft["id"], "prod-a", reason="clean up",
                       action="remove")
        wait(service, "prod-a")

    def test_the_refusal_travels_with_each_target(self):
        service, _audit = build()
        a_draft(service)
        targets = {t["cluster"]: t for t in
                   service.overview(ADMIN)["catalogs"][0]["targets"]}
        self.assertIsNone(targets["dev-a"]["refusal"])
        self.assertIsNotNone(targets["prod-a"]["refusal"])


class RecordTest(unittest.TestCase):
    def test_a_deployment_stores_what_was_sent_by_value(self):
        """⛔ The draft can be edited afterwards. "What did we put on prod-a"
        must stay answerable."""
        service, _audit = build()
        draft = a_draft(service)
        service.deploy(ADMIN, draft["id"], "dev-a", reason="first try")
        wait(service, "dev-a")
        service.save(ADMIN, draft["id"], connector="postgresql",
                     properties={"connection-url": "jdbc:postgresql://db/CHANGED"},
                     reason="changed my mind")

        record = service.repository.recent_deployments()[0]
        self.assertEqual("jdbc:postgresql://db/x",
                         record["properties"]["connection-url"])
        self.assertEqual("SUCCEEDED", record["state"])

    def test_a_failure_is_recorded_not_discarded(self):
        service, _audit = build(
            runner=lambda c, t, on_line: {"rc": 4, "error": "no route to host"})
        draft = a_draft(service)
        service.deploy(ADMIN, draft["id"], "dev-a", reason="first try")
        wait(service, "dev-a")

        record = service.repository.recent_deployments()[0]
        self.assertEqual("FAILED", record["state"])
        self.assertIn("no route", record["detail"])

    def test_every_deploy_lands_an_audit_record_with_a_reason(self):
        service, audit = build()
        draft = a_draft(service)
        service.deploy(ADMIN, draft["id"], "dev-a", reason="reporting cutover")
        wait(service, "dev-a")

        deploys = [r for r in audit.records if r.action_type == "CATALOG_DEPLOY"]
        self.assertEqual(1, len(deploys))
        self.assertEqual("reporting cutover", deploys[0].reason)
        self.assertEqual("dev-a", deploys[0].target_cluster)


class PermissionTest(unittest.TestCase):
    def test_a_viewer_may_look_but_not_deploy(self):
        service, _audit = build()
        draft = a_draft(service)
        service.overview(VIEWER)
        with self.assertRaises(Forbidden):
            service.deploy(VIEWER, draft["id"], "dev-a", reason="no")

    def test_two_deploys_to_one_cluster_do_not_overlap(self):
        import threading

        gate = threading.Event()
        service, _audit = build(
            runner=lambda c, t, on_line: (gate.wait(2), {"rc": 0})[1])
        first = a_draft(service)
        second = a_draft(service, name="other")

        service.deploy(ADMIN, first["id"], "dev-a", reason="one")
        with self.assertRaises(InvalidRequest):
            service.deploy(ADMIN, second["id"], "dev-a", reason="two")
        gate.set()
        wait(service, "dev-a")


if __name__ == "__main__":
    unittest.main()
