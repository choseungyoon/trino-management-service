"""What may be saved into Trino's resource group tables (DESIGN_WL07.md 4).

The database enforces almost none of this. There is no unique constraint on
(name, parent, environment), no check on limit formats, and no notion of which
selector targets which group - so every one of these rules is the only thing
standing between an operator and a cluster that stops admitting queries ten
seconds later.
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.ops.resource_group_rules import (  # noqa: E402
    ERROR,
    WARNING,
    blocking,
    percentage_of,
    validate,
)


def group(gid, **overrides):
    path = gid.split(".")
    row = {
        "id": gid, "path": path, "name": path[-1],
        "max_queued": 100, "hard_concurrency_limit": 10,
        "jmx_export": True,
    }
    row.update(overrides)
    return row


def selector(target, sid=1, **matchers):
    return {"id": sid, "priority": 10, "target": target,
            "matchers": matchers, "catch_all": not matchers}


def codes(findings, level=None):
    return sorted({f.code for f in findings if level is None or f.level == level})


class RequiredFieldTest(unittest.TestCase):
    def test_a_group_needs_a_name_and_both_required_limits(self):
        findings = validate([group("global", name="", max_queued=None,
                                   hard_concurrency_limit=None)], [])
        self.assertIn("V1", codes(findings, ERROR))
        self.assertEqual(2, len([f for f in findings if f.code == "V2"]))

    def test_a_name_longer_than_the_column_is_refused(self):
        findings = validate([group("global", name="x" * 251)], [])
        self.assertIn("V1", codes(findings, ERROR))

    def test_a_zero_limit_is_refused_rather_than_treated_as_tuning(self):
        """Trino accepts 0; it means the group runs nothing. That is a delete,
        not a value."""
        findings = validate([group("global", hard_concurrency_limit=0)], [])
        self.assertIn("V2", codes(findings, ERROR))
        self.assertIn("delete the group", " ".join(f.message for f in findings))


class FormatTest(unittest.TestCase):
    def test_memory_may_be_a_size_or_a_share(self):
        for value in ("100GB", "80%", "1.5TB", "512MB"):
            self.assertEqual([], blocking(validate(
                [group("global", soft_memory_limit=value)],
                [selector("global")])), value)

    def test_memory_in_any_other_shape_is_refused(self):
        findings = validate([group("global", soft_memory_limit="lots")], [])
        self.assertIn("V3", codes(findings, ERROR))

    def test_cpu_limits_are_durations(self):
        findings = validate([group("global", hard_cpu_limit="1GB")], [])
        self.assertIn("V3", codes(findings, ERROR))

    def test_a_soft_cpu_limit_without_a_hard_one_is_refused(self):
        """Trino's documentation requires the pair."""
        findings = validate([group("global", soft_cpu_limit="30m")], [])
        self.assertIn("V4", codes(findings, ERROR))

    def test_scheduling_policy_is_an_enumeration(self):
        findings = validate([group("global", scheduling_policy="round_robin")], [])
        self.assertIn("V5", codes(findings, ERROR))


class HierarchyTest(unittest.TestCase):
    def test_query_priority_must_cover_the_whole_subtree(self):
        findings = validate([
            group("global", scheduling_policy="query_priority"),
            group("global.adhoc"),
        ], [selector("global.adhoc")])
        self.assertIn("V6", codes(findings, ERROR))

    def test_duplicate_paths_are_refused_because_the_database_accepts_them(self):
        findings = validate([group("global"), group("global")], [selector("global")])
        self.assertIn("V9", codes(findings, ERROR))
        self.assertIn("no unique constraint", " ".join(f.message for f in findings))

    def test_sibling_shares_over_the_parent_warn_but_save(self):
        """Legal, and a percentage is of the cluster rather than the parent -
        so this sums to something people rarely intend."""
        findings = validate([
            group("global", soft_memory_limit="80%"),
            group("global.a", soft_memory_limit="50%"),
            group("global.b", soft_memory_limit="50%"),
        ], [selector("global.a"), selector("global.b", sid=2)])
        self.assertEqual([], blocking(findings))
        self.assertIn("W1", codes(findings, WARNING))


class SelectorTest(unittest.TestCase):
    def test_a_selector_pointing_nowhere_is_refused(self):
        findings = validate([group("global")], [selector("global.gone")])
        self.assertIn("V8", codes(findings, ERROR))

    def test_a_tree_with_no_catch_all_is_refused(self):
        """V10 - Trino 477 does not document what happens to an unmatched query,
        so the state is never reachable rather than merely discouraged."""
        findings = validate([group("global")], [selector("global", user_regex="^bob$")])
        self.assertIn("V10", codes(findings, ERROR))

    def test_no_selectors_at_all_is_refused_when_groups_exist(self):
        """The empty case used to pass: the rule was keyed on the selector list,
        so deleting the last one left zero selectors and validation waved it
        through - which is exactly the state V10 exists to prevent. Found by
        tests/integration/smoke_resource_groups.py, against a real table."""
        findings = validate([group("global")], [])
        self.assertIn("V10", codes(findings, ERROR))

    def test_an_empty_environment_is_not_an_error(self):
        """Nothing configured is a different state from misconfigured, and
        refusing it would make the first group impossible to create."""
        self.assertEqual([], blocking(validate([], [])))

    def test_a_broken_regular_expression_is_refused(self):
        findings = validate([group("global")], [selector("global", user_regex="^(unclosed")])
        self.assertIn("V7", codes(findings, ERROR))

    def test_an_over_long_regex_is_refused_at_the_column_limit(self):
        findings = validate([group("global")],
                            [selector("global", user_regex="a" * 513)])
        self.assertIn("V7", codes(findings, ERROR))

    def test_user_group_regex_warns_when_no_group_provider_exists(self):
        """The mistake actually made on this project: a dead selector."""
        findings = validate([group("global")],
                            [selector("global", user_group_regex="admin")],
                            group_provider_configured=False)
        self.assertIn("W4", codes(findings, WARNING))

    def test_the_same_rule_is_silent_when_a_provider_is_configured(self):
        findings = validate([group("global")],
                            [selector("global", user_group_regex="admin")],
                            group_provider_configured=True)
        self.assertNotIn("W4", codes(findings, WARNING))

    def test_an_unreachable_leaf_warns(self):
        findings = validate([group("global"), group("global.orphan")],
                            [selector("global")])
        self.assertIn("W5", codes(findings, WARNING))

    def test_a_parent_with_children_is_not_called_unreachable(self):
        """Parents legitimately have no selector of their own."""
        findings = validate([group("global"), group("global.adhoc")],
                            [selector("global.adhoc")])
        self.assertNotIn("W5", [f.code for f in findings if f.target == "global"])


class WarningTest(unittest.TestCase):
    def test_quotas_warn_about_the_failure_mode_people_do_not_predict(self):
        findings = validate([group("global", hard_physical_data_scan_limit="10GB")],
                            [selector("global")])
        self.assertEqual([], blocking(findings))
        message = " ".join(f.message for f in findings if f.code == "W3")
        self.assertIn("queue until the quota period rolls", message)

    def test_a_group_without_jmx_export_warns_that_it_stays_invisible(self):
        findings = validate([group("global", jmx_export=False)], [selector("global")])
        self.assertIn("W2", codes(findings, WARNING))

    def test_a_healthy_tree_produces_nothing_blocking(self):
        findings = validate([
            group("global", soft_memory_limit="80%"),
            group("global.${USER}", soft_memory_limit="30%", jmx_export=False,
                  hard_concurrency_limit=8),
            group("admin", hard_concurrency_limit=20),
        ], [
            selector("admin", sid=1, user_regex=r"^datalake\.admin$"),
            selector("global.${USER}", sid=2),
        ])
        self.assertEqual([], blocking(findings))


class PercentageTest(unittest.TestCase):
    def test_shares_parse_and_sizes_do_not(self):
        self.assertEqual(80.0, percentage_of("80%"))
        self.assertIsNone(percentage_of("1GB"))
        self.assertIsNone(percentage_of(None))


if __name__ == "__main__":
    unittest.main()
