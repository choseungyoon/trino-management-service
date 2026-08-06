"""Tests for tms.core.config.

Two properties matter most here and are asserted explicitly:

1. Secrets never render. The repository is PUBLIC (DECISIONS.md D-002) and
   config objects end up in tracebacks and debug logs.
2. A stale threshold shorter than the poll interval is rejected. Otherwise every
   snapshot is stale the instant it is written and the whole UI shows UNKNOWN.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.core.config import (  # noqa: E402
    Config,
    ConfigError,
    Secret,
    build_config,
    load_config,
)

MINIMAL = {
    "clusters": [
        {"name": "prod-a", "coordinator_url": "https://a.invalid:8443/", "expected_workers": 12}
    ],
    "trino": {"user": "tms-svc", "password": "pw"},
    "database": {"url": "postgresql://tms:pw@db.invalid/tms"},
}


class SecretTest(unittest.TestCase):
    def test_secret_does_not_render_its_value(self):
        secret = Secret("hunter2")
        self.assertNotIn("hunter2", repr(secret))
        self.assertNotIn("hunter2", str(secret))
        self.assertNotIn("hunter2", "{}".format(secret))
        self.assertNotIn("hunter2", "{!r}".format(secret))
        self.assertEqual(secret.reveal(), "hunter2")

    def test_config_repr_does_not_leak_credentials(self):
        config = build_config(MINIMAL)
        rendered = repr(config)
        self.assertNotIn("pw", rendered.replace("Secret(***)", ""))
        self.assertIn("Secret(***)", rendered)


class BuildConfigTest(unittest.TestCase):
    def test_minimal_config_builds(self):
        config = build_config(MINIMAL)
        self.assertIsInstance(config, Config)
        self.assertEqual(config.cluster_names, ["prod-a"])
        self.assertEqual(config.trino.user, "tms-svc")

    def test_trailing_slash_is_stripped_from_coordinator_url(self):
        config = build_config(MINIMAL)
        self.assertEqual(config.cluster("prod-a").coordinator_url, "https://a.invalid:8443")

    def test_missing_password_is_rejected(self):
        raw = {**MINIMAL, "trino": {"user": "tms-svc"}}
        with self.assertRaises(ConfigError) as ctx:
            build_config(raw)
        self.assertIn("trino.password", str(ctx.exception))

    def test_missing_database_url_is_rejected(self):
        raw = {k: v for k, v in MINIMAL.items() if k != "database"}
        with self.assertRaises(ConfigError):
            build_config(raw)

    def test_no_clusters_is_rejected(self):
        raw = {**MINIMAL, "clusters": []}
        with self.assertRaises(ConfigError):
            build_config(raw)

    def test_duplicate_cluster_names_are_rejected(self):
        raw = {
            **MINIMAL,
            "clusters": [MINIMAL["clusters"][0], MINIMAL["clusters"][0]],
        }
        with self.assertRaises(ConfigError) as ctx:
            build_config(raw)
        self.assertIn("duplicate", str(ctx.exception))

    def test_stale_threshold_below_poll_interval_is_rejected(self):
        """Otherwise every snapshot is stale the moment the collector writes it."""
        raw = {
            **MINIMAL,
            "collector": {"query_poll_interval_seconds": 30, "stale_threshold_seconds": 5},
        }
        with self.assertRaises(ConfigError) as ctx:
            build_config(raw)
        self.assertIn("stale_threshold_seconds", str(ctx.exception))

    def test_gateway_enabled_without_base_url_is_rejected(self):
        raw = {**MINIMAL, "gateway": {"enabled": True, "base_url": ""}}
        with self.assertRaises(ConfigError):
            build_config(raw)

    def test_gateway_disabled_by_default(self):
        """B6 is unresolved, so the Gateway adapter stays off until confirmed."""
        self.assertFalse(build_config(MINIMAL).gateway.enabled)

    def test_coordinator_is_counted_in_active_nodes_by_default(self):
        """Verified 2026-08-06: a 12-worker cluster reports ActiveNodeCount 13."""
        self.assertTrue(build_config(MINIMAL).trino_facts.coordinator_counted_in_active_nodes)

    def test_negative_expected_workers_is_rejected(self):
        raw = {
            **MINIMAL,
            "clusters": [
                {"name": "a", "coordinator_url": "https://a.invalid", "expected_workers": -1}
            ],
        }
        with self.assertRaises(ConfigError):
            build_config(raw)

    def test_deeplinks_default_to_empty_so_no_dead_links_render(self):
        deeplinks = build_config(MINIMAL).deeplinks
        self.assertEqual(deeplinks.log_template, "")
        self.assertEqual(deeplinks.query_history_url_template, "")


class LoadConfigTest(unittest.TestCase):
    def _write(self, directory, name, body):
        path = os.path.join(directory, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body)
        return path

    def test_secret_file_overlays_base_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._write(
                tmp,
                "config.yaml",
                "clusters:\n"
                "  - name: prod-a\n"
                "    coordinator_url: https://a.invalid:8443\n"
                "    expected_workers: 12\n"
                "trino:\n"
                "  user: tms-svc\n",
            )
            self._write(
                tmp,
                "config.secret.yaml",
                "trino:\n  password: from-secret-file\n"
                "database:\n  url: postgresql://tms:x@db.invalid/tms\n",
            )
            config = load_config(base, environ={})
            self.assertEqual(config.trino.password.reveal(), "from-secret-file")
            # The overlay must not clobber sibling keys in the same section.
            self.assertEqual(config.trino.user, "tms-svc")

    def test_environment_wins_over_secret_file(self):
        """systemd injects credentials via EnvironmentFile; that must take priority."""
        with tempfile.TemporaryDirectory() as tmp:
            base = self._write(
                tmp,
                "config.yaml",
                "clusters:\n"
                "  - name: prod-a\n"
                "    coordinator_url: https://a.invalid:8443\n"
                "    expected_workers: 12\n"
                "trino:\n"
                "  user: tms-svc\n",
            )
            self._write(
                tmp,
                "config.secret.yaml",
                "trino:\n  password: from-file\n"
                "database:\n  url: postgresql://tms:x@db.invalid/tms\n",
            )
            config = load_config(base, environ={"TMS_TRINO_PASSWORD": "from-env"})
            self.assertEqual(config.trino.password.reveal(), "from-env")

    def test_empty_environment_value_does_not_override(self):
        """An unset EnvironmentFile entry must not blank out a real secret."""
        with tempfile.TemporaryDirectory() as tmp:
            base = self._write(
                tmp,
                "config.yaml",
                "clusters:\n"
                "  - name: prod-a\n"
                "    coordinator_url: https://a.invalid:8443\n"
                "    expected_workers: 12\n"
                "trino:\n"
                "  user: tms-svc\n"
                "  password: from-file\n"
                "database:\n"
                "  url: postgresql://tms:x@db.invalid/tms\n",
            )
            config = load_config(base, environ={"TMS_TRINO_PASSWORD": ""})
            self.assertEqual(config.trino.password.reveal(), "from-file")


class ShippedConfigTest(unittest.TestCase):
    """The tracked config.yaml must stay loadable and free of secrets."""

    def setUp(self):
        self.repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.path = os.path.join(self.repo, "config", "config.yaml")

    def test_shipped_config_loads_with_injected_secrets(self):
        config = load_config(
            self.path,
            secret_path=os.path.join(self.repo, "config", "does-not-exist.yaml"),
            environ={
                "TMS_TRINO_PASSWORD": "x",
                "TMS_DATABASE_URL": "postgresql://tms:x@db.invalid/tms",
            },
        )
        self.assertEqual(config.trino.user, "tms-svc")
        self.assertEqual(len(config.clusters), 2)

    def test_shipped_config_contains_no_password_key(self):
        with open(self.path, "r", encoding="utf-8") as handle:
            body = handle.read()
        for forbidden in ("password:", "secret:", "PRIVATE KEY"):
            self.assertNotIn(forbidden, body, "{} must not appear in a public repo".format(forbidden))


if __name__ == "__main__":
    unittest.main(verbosity=2)
