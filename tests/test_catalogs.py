"""Catalog drafts: the two rules that decide whether one may be deployed.

⛔ Both exist because of measurements, not caution. A catalog file Trino cannot
load stops the whole server from starting (T1-9-1) and TMS has no way to check
it in advance (T1-9-3) - so the development cluster is the validator. And a
catalog holds `connection-password`, which Trino will read from the node's
environment (T1-9-2), so TMS never has to hold it.
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from tms.ops.catalogs import (  # noqa: E402
    CatalogError,
    deployable,
    environment_references,
    fingerprint,
    refuse_deploy,
    render,
    validate,
)

DEV = ["dev-a"]


class CredentialTest(unittest.TestCase):
    """⛔ No secret enters TMS's database, API, audit log or screens."""

    def test_a_plaintext_credential_is_refused(self):
        for key in ("connection-password", "s3.aws-secret-key",
                    "http-server.https.keystore.key", "some.private-key"):
            with self.assertRaises(CatalogError, msg=key) as caught:
                validate("c", "postgresql", {key: "hunter2"})
            # The refusal has to teach the alternative, not just say no.
            self.assertIn("${ENV:", str(caught.exception))

    def test_an_environment_reference_is_accepted(self):
        draft = validate("c", "postgresql",
                         {"connection-password": "${ENV:PG_PASSWORD}"})
        self.assertEqual("${ENV:PG_PASSWORD}",
                         draft["properties"]["connection-password"])

    def test_a_reference_with_junk_around_it_is_still_refused(self):
        """`prefix${ENV:X}` is a literal with a reference glued on - Trino
        resolves it, but half the value is still a secret sitting in TMS."""
        with self.assertRaises(CatalogError):
            validate("c", "postgresql",
                     {"connection-password": "pre${ENV:PG_PASSWORD}"})

    def test_ordinary_properties_are_untouched(self):
        draft = validate("c", "postgresql",
                         {"connection-url": "jdbc:postgresql://db/x"})
        self.assertEqual("jdbc:postgresql://db/x",
                         draft["properties"]["connection-url"])

    def test_the_variables_a_deploy_needs_are_listed(self):
        """⛔ A reference whose variable is absent stops the server booting,
        exactly like a bad connector name. Saying so beforehand beats finding
        out during a restart."""
        self.assertEqual(
            ["PG_PASSWORD", "S3_KEY"],
            environment_references({"connection-password": "${ENV:PG_PASSWORD}",
                                    "s3.aws-access-key": "${ENV:S3_KEY}",
                                    "connection-url": "jdbc:x"}))


class ShapeTest(unittest.TestCase):
    def test_the_name_is_a_filename(self):
        for bad in ("Upper", "has-dash", "1leading", "", "with space"):
            with self.assertRaises(CatalogError, msg=bad):
                validate(bad, "memory", {})

    def test_the_connector_is_not_checked_against_a_list(self):
        """⛔ TMS has no way to know which connectors this build has (T1-9-3).
        A hand-written list would be a second opinion about a build it has
        never seen; a wrong name is caught by the development cluster."""
        draft = validate("c", "some_connector_tms_never_heard_of", {})
        self.assertEqual("some_connector_tms_never_heard_of", draft["connector"])

    def test_the_shape_trino_writes_is_enforced(self):
        """`delta-lake` is the plugin directory; `delta_lake` is the connector."""
        with self.assertRaises(CatalogError) as caught:
            validate("c", "delta-lake", {})
        self.assertIn("delta_lake", str(caught.exception))

    def test_connector_name_is_not_a_property(self):
        with self.assertRaises(CatalogError):
            validate("c", "memory", {"connector.name": "hive"})

    def test_the_rendered_file_leads_with_the_connector(self):
        text = render("postgresql", {"b": "2", "a": "1"})
        self.assertEqual("connector.name=postgresql\na=1\nb=2\n", text)


class DevelopmentGateTest(unittest.TestCase):
    """⛔ D-018. Not caution - the only method available (T1-9-3)."""

    def test_an_unproved_draft_may_go_to_the_development_cluster(self):
        self.assertIsNone(refuse_deploy({}, "dev-a", DEV))

    def test_an_unproved_draft_may_not_go_to_production(self):
        refusal = refuse_deploy({}, "prod-a", DEV)
        self.assertIn("dev-a", refusal)
        # It has to say *why*, or it reads as bureaucracy.
        self.assertIn("stops every node", refusal)

    def test_a_proved_draft_may_go_to_production(self):
        self.assertIsNone(
            refuse_deploy({"verified_on": "dev-a"}, "prod-a", DEV))

    def test_being_proved_on_another_production_cluster_does_not_count(self):
        refusal = refuse_deploy({"verified_on": "prod-b"}, "prod-a", DEV)
        self.assertIn("prod-b", refusal)

    def test_with_no_development_cluster_nothing_reaches_production(self):
        """⛔ Fails closed. "Nowhere to prove it" must not read as "no gate"."""
        refusal = refuse_deploy({"verified_on": "prod-b"}, "prod-a", [])
        self.assertIn("development_clusters", refusal)

    def test_the_per_cluster_answer_is_one_call(self):
        """The screen greys a button with this and the service raises with it.
        Two copies of the rule would disagree the day one was edited."""
        rows = deployable({}, ["dev-a", "prod-a"], DEV)
        self.assertTrue(rows[0]["development"])
        self.assertIsNone(rows[0]["refusal"])
        self.assertIsNotNone(rows[1]["refusal"])


class FingerprintTest(unittest.TestCase):
    def test_editing_a_draft_changes_its_fingerprint(self):
        """⛔ Otherwise somebody proves a working catalog, changes a property,
        and ships the change on the strength of a test that never saw it."""
        before = fingerprint("postgresql", {"connection-url": "jdbc:a"})
        after = fingerprint("postgresql", {"connection-url": "jdbc:b"})
        self.assertNotEqual(before, after)

    def test_property_order_is_not_a_change(self):
        self.assertEqual(fingerprint("memory", {"a": "1", "b": "2"}),
                         fingerprint("memory", {"b": "2", "a": "1"}))


if __name__ == "__main__":
    unittest.main()
