"""Where nodes disagree about their configuration.

⛔ The judgement being tested is what counts as drift. A coordinator and a
worker are *supposed* to differ; a drift screen that says so on every healthy
cluster is a screen nobody reads. And a credential must not travel from a
node's catalog file into TMS's database on the way to that screen.
"""

import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from tms.ops.configscan import (  # noqa: E402
    MARKER,
    REDACTED,
    compare,
    parse_properties,
    parse_scan,
    redact,
)


def scan_line(host, role="worker", config="coordinator=false\n",
              properties=None, valid_names=("query.max-memory", "http-server.http.port"),
              files=None, **extra):
    payload = {
        "host": host, "role": role, "reachable": True,
        "files": files if files is not None else {
            "etc/config.properties": {"present": True, "content": config},
        },
        "properties": properties or {},
        "valid_names": list(valid_names),
    }
    payload.update(extra)
    return MARKER + json.dumps(payload)


class RedactionTest(unittest.TestCase):
    """⛔ `etc/catalog/*` holds `connection-password`. Nothing here may carry a
    credential into TMS's database on its way to a screen."""

    def test_credential_shaped_keys_lose_their_values(self):
        cleaned = redact({
            "connection-password": "hunter2",
            "s3.aws-secret-key": "abc",
            "http-server.https.keystore.key": "k",
            "internal-communication.shared-secret": "s",
            "auth-token": "t",
            "connection-url": "jdbc:postgresql://db/x",
        })
        self.assertEqual(REDACTED, cleaned["connection-password"])
        self.assertEqual(REDACTED, cleaned["s3.aws-secret-key"])
        self.assertEqual(REDACTED, cleaned["http-server.https.keystore.key"])
        self.assertEqual(REDACTED, cleaned["internal-communication.shared-secret"])
        self.assertEqual(REDACTED, cleaned["auth-token"])
        # Not a credential. Redacting everything would make the screen useless.
        self.assertEqual("jdbc:postgresql://db/x", cleaned["connection-url"])

    def test_catalog_files_are_never_collected_by_content(self):
        """Checksum only. "Do these nodes have the same catalog" is answerable
        without "what is in it"."""
        line = scan_line("w1", files={
            "etc/catalog/hive.properties": {
                "present": True, "sha256": "abc123",
                "content": "connection-password=hunter2"},
        })
        node = parse_scan([line])[0]
        entry = node["files"]["etc/catalog/hive.properties"]
        self.assertFalse(entry["content_collected"])
        self.assertNotIn("properties", entry)
        self.assertEqual("abc123", entry["sha256"])


class ParseTest(unittest.TestCase):
    def test_a_properties_file_is_read_the_way_trino_reads_one(self):
        parsed = parse_properties("# comment\n! also\nquery.max-memory=900GB\n"
                                  "spaced = yes \n\nnonsense\n")
        self.assertEqual({"query.max-memory": "900GB", "spaced": "yes"}, parsed)

    def test_ansible_chatter_around_the_line_is_ignored(self):
        noise = ['ok: [w1] => {"msg": "something"}',
                 'TASK [collect] ' + '*' * 40,
                 'ok: [w1] => ' + scan_line("w1")]
        self.assertEqual(["w1"], [n["host"] for n in parse_scan(noise)])

    def test_an_unreadable_line_becomes_an_error_not_a_silence(self):
        """⛔ A node missing from the comparison reads as "everything agrees"."""
        nodes = parse_scan([MARKER + "{not json"])
        self.assertEqual(1, len(nodes))
        self.assertFalse(nodes[0]["reachable"])
        self.assertIn("could not read", nodes[0]["error"])


class RoleTest(unittest.TestCase):
    """⛔ The core judgement: compare within a role, never across."""

    def test_a_coordinator_and_a_worker_differing_is_not_drift(self):
        result = compare(parse_scan([
            scan_line("c1", role="coordinator", config="coordinator=true\n",
                      properties={"coordinator": "true", "query.max-memory": "900GB"}),
            scan_line("w1", role="worker", config="coordinator=false\n",
                      properties={"coordinator": "false", "query.max-memory": "900GB"}),
        ]))
        self.assertTrue(result["agree"], result["findings"])

    def test_two_workers_differing_is_drift(self):
        result = compare(parse_scan([
            scan_line("w1", properties={"http-server.http.port": "8443"}),
            scan_line("w2", properties={"http-server.http.port": "8080"}),
        ]))
        self.assertFalse(result["agree"])
        finding = next(f for f in result["findings"] if f["kind"] == "value_differs")
        self.assertEqual("http-server.http.port", finding["subject"])
        self.assertEqual({"w1": "8443", "w2": "8080"}, finding["hosts"])

    def test_one_node_of_a_role_is_never_drift(self):
        """A cluster with one coordinator is every cluster."""
        result = compare(parse_scan([scan_line("c1", role="coordinator")]))
        self.assertTrue(result["agree"])

    def test_a_file_missing_from_one_worker_is_named(self):
        result = compare(parse_scan([
            scan_line("w1", files={"etc/access-control.properties":
                                   {"present": True, "sha256": "aaa"}}),
            scan_line("w2", files={"etc/access-control.properties":
                                   {"present": False}}),
        ]))
        finding = next(f for f in result["findings"] if f["kind"] == "missing_file")
        self.assertEqual("etc/access-control.properties", finding["subject"])
        self.assertIn("w2", finding["detail"])

    def test_node_properties_differing_is_marked_expected(self):
        """⛔ `node.id` is unique per node. It differs on every healthy
        cluster, so it must not read like a fault."""
        result = compare(parse_scan([
            scan_line("w1", files={"etc/node.properties":
                                   {"present": True, "sha256": "aaa"}}),
            scan_line("w2", files={"etc/node.properties":
                                   {"present": True, "sha256": "bbb"}}),
        ]))
        finding = next(f for f in result["findings"]
                       if f["subject"] == "etc/node.properties")
        self.assertTrue(finding["expected"])


class UnreachableTest(unittest.TestCase):
    def test_a_node_that_did_not_answer_is_a_finding(self):
        """What the others agree on says nothing about the one that is silent."""
        result = compare(parse_scan([
            scan_line("w1"),
            scan_line("w2", reachable=False, error="ssh timed out"),
        ]))
        finding = next(f for f in result["findings"] if f["kind"] == "unreachable")
        self.assertIn("w2", finding["subject"])

    def test_a_development_cluster_does_not_report_a_missing_worker(self):
        """Its worker count changes with whatever is being tested (D-018)."""
        result = compare(parse_scan([
            scan_line("w1"),
            scan_line("w2", reachable=False, error="host is down"),
        ]), ignore_missing_nodes=True)
        self.assertEqual([], [f for f in result["findings"]
                              if f["kind"] == "unreachable"])


class ValidNamesTest(unittest.TestCase):
    """⛔ TMS does not decide what a valid property name is. Trino does, and it
    refuses to boot on one it does not know (T1-8-1)."""

    def test_the_shared_list_is_the_intersection_not_the_union(self):
        """A deploy goes to several nodes at once, so a name only one node
        knows is a name that would stop the others booting."""
        result = compare(parse_scan([
            scan_line("w1", valid_names=("a", "b", "plugin-only")),
            scan_line("w2", valid_names=("a", "b")),
        ]))
        self.assertEqual(["a", "b"], result["valid_names"])

    def test_no_names_collected_means_no_claim(self):
        """Empty, not "everything is invalid" - a deploy check that refuses
        every name because collection failed is worse than no check."""
        result = compare(parse_scan([scan_line("w1", valid_names=())]))
        self.assertEqual([], result["valid_names"])


if __name__ == "__main__":
    unittest.main()


class TransportTest(unittest.TestCase):
    def test_content_arrives_base64_and_is_decoded(self):
        """The playbook sends `slurp`'s base64: a config file is several lines
        and the transport is one line of JSON."""
        import base64

        blob = base64.b64encode(b"coordinator=false\nquery.max-memory=900GB\n")
        node = parse_scan([MARKER + json.dumps({
            "host": "w1", "role": "worker", "reachable": True,
            "files": {"etc/config.properties": {
                "present": True, "content_b64": blob.decode()}},
            "valid_names": [],
        })])[0]
        self.assertEqual("900GB", node["properties"]["query.max-memory"])

    def test_undecodable_content_is_absent_not_guessed(self):
        node = parse_scan([MARKER + json.dumps({
            "host": "w1", "role": "worker", "reachable": True,
            "files": {"etc/config.properties": {
                "present": True, "content_b64": "!!!not base64!!!"}},
            "valid_names": [],
        })])[0]
        self.assertEqual({}, node["properties"])

    def test_the_file_is_what_a_deploy_would_overwrite(self):
        """⛔ Values come from `etc/config.properties`, not from the 447-line
        startup dump - most of that is a default nobody set, and the file is
        what an operator edits."""
        import base64

        node = parse_scan([MARKER + json.dumps({
            "host": "w1", "role": "worker", "reachable": True,
            "files": {"etc/config.properties": {
                "present": True,
                "content_b64": base64.b64encode(b"a=1\n").decode()}},
            "valid_names": ["a", "b", "c"],
        })])[0]
        self.assertEqual({"a": "1"}, node["properties"])
        self.assertEqual(["a", "b", "c"], node["valid_names"])
