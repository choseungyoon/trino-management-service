"""Tests for the pre-restart configuration check.

Each case here is a mistake that actually reached production during the first
deploy, or one the same class of edit would produce. The point of the tool is
that these fail at check time instead of at 3am.

Only static checks are exercised - the live ones need a Trino.
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.core.config import build_config  # noqa: E402
from tms.core.configcheck import (  # noqa: E402
    FAIL,
    OK,
    WARN,
    Report,
    check_clusters,
    check_deeplinks,
    check_portal,
    check_secret_file,
    run,
)

HASH = "pbkdf2_sha256$600000$abc$def"


@contextlib.contextmanager
def quiet():
    """The checker prints as it goes; keep that out of the test log."""
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def config(**overrides):
    raw = {
        "clusters": [{"name": "prod-a", "coordinator_url": "https://a.invalid:8443",
                      "expected_workers": 12}],
        "trino": {"user": "tms-svc", "password": "pw"},
        "database": {"url": "postgresql://u:p@h:5432/d"},
        "portal": {"session_secret": "s" * 40,
                   "local_users": {"op": {"password_hash": HASH, "roles": ["admin"]}}},
    }
    raw.update(overrides)
    return build_config(raw)


def levels(report, contains):
    return [row[0] for row in report.rows if contains in row[1]]


def run_check(func, *args):
    report = Report()
    with quiet():
        func(report, *args)
    return report


def run_cli(argv):
    with quiet():
        return run(argv)


def write_config(directory, coordinator_url="https://a.invalid:8443"):
    path = os.path.join(directory, "config.yaml")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(
            "clusters:\n"
            "  - name: prod-a\n"
            "    coordinator_url: {}\n"
            "    expected_workers: 12\n"
            "trino:\n"
            "  user: tms-svc\n"
            "  password: pw\n"
            "database:\n"
            "  url: postgresql://u:p@h:5432/d\n"
            "portal:\n"
            "  session_secret: {}\n".format(coordinator_url, "s" * 40)
        )
    return path


class ClusterCheckTest(unittest.TestCase):
    def test_http_coordinator_fails(self):
        """Basic auth only works over HTTPS - this leaves every JMX test on 401."""
        cfg = config(clusters=[{"name": "prod-a",
                                "coordinator_url": "http://a.invalid:8080",
                                "expected_workers": 12}])
        self.assertIn(FAIL, levels(run_check(check_clusters, cfg), "coordinator_url"))

    def test_https_coordinator_passes(self):
        report = run_check(check_clusters, config())
        self.assertEqual([OK], levels(report, "coordinator_url"))

    def test_zero_expected_workers_warns(self):
        cfg = config(clusters=[{"name": "prod-a",
                                "coordinator_url": "https://a.invalid:8443",
                                "expected_workers": 0}])
        self.assertIn(WARN, levels(run_check(check_clusters, cfg), "expected_workers"))


class PortalCheckTest(unittest.TestCase):
    def test_no_local_users_warns_because_the_ui_disappears(self):
        cfg = config(portal={"session_secret": "s" * 40, "local_users": {}})
        self.assertIn(WARN, levels(run_check(check_portal, cfg), "local_users"))

    def test_missing_session_secret_with_users_fails(self):
        """tms-api refuses to start in this state."""
        cfg = config(portal={
            "session_secret": "",
            "local_users": {"op": {"password_hash": HASH, "roles": ["admin"]}}})
        self.assertIn(FAIL, levels(run_check(check_portal, cfg), "session_secret"))

    def test_temporary_password_is_flagged(self):
        """Not reflecting the new hash back into the file silently reverts it."""
        cfg = config(portal={
            "session_secret": "s" * 40,
            "local_users": {"op": {"password_hash": HASH, "roles": ["admin"],
                                   "must_change_password": True}}})
        self.assertIn(WARN, levels(run_check(check_portal, cfg), "임시 비밀번호"))


class DeeplinkCheckTest(unittest.TestCase):
    def test_missing_links_warn_but_do_not_fail(self):
        """Empty means the link is not rendered - deliberate, not broken."""
        report = run_check(check_deeplinks, config())
        self.assertIn(WARN, levels(report, "딥링크"))
        self.assertNotIn(FAIL, [row[0] for row in report.rows])

    def test_template_missing_its_placeholder_warns(self):
        cfg = config(deeplinks={
            "query_history": {"query_url_template": "https://hist.invalid/"}})
        report = run_check(check_deeplinks, cfg)
        self.assertIn(WARN, levels(report, "query_history.query_url_template"))

    def test_complete_template_passes(self):
        cfg = config(deeplinks={
            "query_history": {"query_url_template": "https://h.invalid/{query_id}"}})
        report = run_check(check_deeplinks, cfg)
        self.assertNotIn(WARN, levels(report, "query_history.query_url_template"))


class SecretFileCheckTest(unittest.TestCase):
    def test_world_readable_secret_warns(self):
        if os.geteuid() == 0:
            self.skipTest("running as root - permission bits do not apply")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.yaml")
            secret = os.path.join(tmp, "config.secret.yaml")
            open(path, "w").close()
            with open(secret, "w", encoding="utf-8") as handle:
                handle.write("trino:\n  password: x\n")
            os.chmod(secret, 0o644)
            report = run_check(check_secret_file, path)
        self.assertIn(WARN, levels(report, "권한"))

    def test_absent_secret_file_is_fine(self):
        """Credentials may come from the environment instead."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.yaml")
            open(path, "w").close()
            report = run_check(check_secret_file, path)
        self.assertEqual([OK], levels(report, "config.secret.yaml"))


class ExitCodeTest(unittest.TestCase):
    def test_missing_config_file_returns_2(self):
        self.assertEqual(2, run_cli(["--config", "/definitely/not/here.yaml"]))

    def test_clean_config_returns_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp)
            self.assertEqual(0, run_cli(["--config", path, "--offline"]))

    def test_http_coordinator_returns_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, coordinator_url="http://a.invalid:8080")
            self.assertEqual(1, run_cli(["--config", path, "--offline"]))


if __name__ == "__main__":
    unittest.main()


class MigrationCoverageTest(unittest.TestCase):
    """`tms-config-check` must know about every migration.

    Fourth instance of the drift shape that has bitten this project three
    times. Here the failure is subtler: config-check would report "필요한
    스키마가 모두 적용되어 있다" while a migration it has never heard of sits
    unapplied - a green check that means nothing.
    """

    def test_every_migration_is_checked_for(self):
        import pathlib

        from tms.core.configcheck import _REQUIRED_OBJECTS

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        on_disk = {p.name for p in pathlib.Path(root, "migrations").glob("*.sql")}
        checked = {name for name, _kind, _value in _REQUIRED_OBJECTS}

        # 002 grants privileges and 005 extends them; neither creates an object
        # a client connection can observe, so they are legitimately unchecked.
        grants_only = {"002_grants.sql", "005_restart_sequence_grants.sql",
                       "011_resource_group_grants.sql",
                       "013_fleet_job_grants.sql",
                       "015_work_board_grants.sql",
                       "017_benchmark_grants.sql",
                       "019_benchmark_query_set_grants.sql"}
        unchecked = on_disk - checked - grants_only
        self.assertEqual(
            set(), unchecked,
            "these migrations exist but tms-config-check does not verify them, "
            "so it would report a clean schema while they are unapplied: "
            "{}".format(sorted(unchecked)))

    def test_the_checked_migrations_all_exist(self):
        import pathlib

        from tms.core.configcheck import _REQUIRED_OBJECTS

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name, _kind, _value in _REQUIRED_OBJECTS:
            self.assertTrue(
                pathlib.Path(root, "migrations", name).is_file(),
                "config-check refers to {} which is not in migrations/".format(name))
