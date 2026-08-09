"""Tests for the view-model builders.

These sit between the API envelopes and the templates. They were at 0%
coverage while the UI was already running in production - the only thing
exercising them was an ad-hoc smoke script outside the repository.

The cases that matter are the degraded ones. Every screen has to survive a
cluster that is unreachable, a health test that returned nothing, and a
snapshot that is stale, without either crashing or quietly rendering a
reassuring zero.
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.web import views  # noqa: E402
from tms.web.views import (  # noqa: E402
    audit_chips,
    cluster_summary,
    expand_state_filter,
    health_view,
    link_rows,
    query_chips,
    state_counts,
    test_observed_text,
)


def health_envelope(tests=None, rollup="GOOD", stale=False, collected_at="2026-08-08T12:00:00Z"):
    return {
        "collected_at": collected_at,
        "stale": stale,
        "data": {"rollup_state": rollup, "tests": tests or []},
    }


class LinkRowsTest(unittest.TestCase):
    def test_known_ids_get_icons_and_descriptions(self):
        rows = link_rows({"links": [
            {"id": "grafana", "label": "Grafana", "url": "https://g.invalid"},
            {"id": "query_history", "label": "Query History", "url": "https://h.invalid"},
        ]})
        self.assertEqual(["grafana", "history"], [r["icon"] for r in rows])
        self.assertEqual("Metrics & dashboards", rows[0]["description"])

    def test_per_cluster_trino_ui_ids_are_recognised(self):
        """The id carries the cluster name, so it cannot be a table lookup."""
        rows = link_rows({"links": [
            {"id": "trino_ui_prod-a", "label": "Trino UI (prod-a)", "url": "https://t.invalid"}]})
        self.assertEqual("trino", rows[0]["icon"])
        self.assertEqual("Coordinator web UI", rows[0]["description"])

    def test_unknown_id_falls_back_without_crashing(self):
        rows = link_rows({"links": [{"id": "something-new", "url": "https://x.invalid"}]})
        self.assertEqual("external", rows[0]["icon"])
        self.assertEqual("something-new", rows[0]["label"], "label falls back to the id")

    def test_empty_and_missing_payloads(self):
        self.assertEqual([], link_rows({}))
        self.assertEqual([], link_rows({"links": None}))


class ClusterSummaryTest(unittest.TestCase):
    def test_reads_workers_and_failure_rate_from_health_tests(self):
        envelope = health_envelope(tests=[
            {"id": "H-03", "name": "Worker registration", "state": "GOOD",
             "observed_value": {"active_workers": 11, "planned_out": 1}},
            {"id": "H-05", "name": "Query failure rate (5m)", "state": "GOOD",
             "observed_value": 2.5},
        ])
        card = cluster_summary("prod-a", 12, envelope,
                               {"data": {"summary": {"running": 7, "queued": 2}}})
        self.assertEqual(11, card["active_workers"])
        self.assertEqual(1, card["planned_out"])
        self.assertEqual(2.5, card["failure_rate"])
        self.assertEqual(7, card["running"])
        self.assertEqual(2, card["queued"])

    def test_unreachable_cluster_yields_none_not_zero(self):
        """An unreachable cluster must not render "0 workers active" - that
        reads as a total outage rather than as missing data."""
        card = cluster_summary("prod-b", 12, health_envelope(rollup="UNKNOWN", stale=True))
        self.assertIsNone(card["active_workers"])
        self.assertIsNone(card["failure_rate"])
        self.assertEqual("UNKNOWN", card["rollup_state"])
        self.assertTrue(card["stale"])

    def test_missing_queries_envelope_leaves_counts_at_zero(self):
        card = cluster_summary("prod-a", 12, health_envelope(), None)
        self.assertEqual(0, card["running"])
        self.assertEqual(0, card["queued"])

    def test_absent_stale_flag_is_treated_as_stale(self):
        """Defaulting to fresh would present unknown freshness as current."""
        card = cluster_summary("prod-a", 12, {"data": {}})
        self.assertTrue(card["stale"])
        self.assertEqual("UNKNOWN", card["rollup_state"])

    def test_h03_with_a_scalar_observation_does_not_crash(self):
        """Shape drift must degrade, not raise."""
        card = cluster_summary("prod-a", 12, health_envelope(tests=[
            {"id": "H-03", "state": "UNKNOWN", "observed_value": "no reading"}]))
        self.assertIsNone(card["active_workers"])

    def test_h05_with_a_dict_observation_is_ignored(self):
        card = cluster_summary("prod-a", 12, health_envelope(tests=[
            {"id": "H-05", "state": "UNKNOWN", "observed_value": {"unexpected": 1}}]))
        self.assertIsNone(card["failure_rate"])


class StateCountsTest(unittest.TestCase):
    def test_counts_by_state(self):
        counts = state_counts([
            {"state": "GOOD"}, {"state": "GOOD"},
            {"state": "BAD"}, {"state": "UNKNOWN"},
        ])
        self.assertEqual({"good": 2, "concerning": 0, "bad": 1, "unknown": 1}, counts)

    def test_missing_state_counts_as_unknown(self):
        self.assertEqual(1, state_counts([{}])["unknown"])

    def test_unrecognised_state_is_dropped_not_miscounted(self):
        counts = state_counts([{"state": "WAT"}])
        self.assertEqual({"good": 0, "concerning": 0, "bad": 0, "unknown": 0}, counts)


class ObservedTextTest(unittest.TestCase):
    def test_worker_observation_is_rendered_as_a_sentence(self):
        text = str(test_observed_text({
            "id": "H-03",
            "observed_value": {"active_workers": 11, "expected_workers": 12,
                               "planned_out": 1, "unplanned_missing": 0}}))
        self.assertIn("11", text)
        self.assertIn("12", text)

    def test_markup_is_not_escaped(self):
        """Returned as Markup so the emphasis renders as HTML, not as text.
        This shipped broken once - '<b>0 of 0</b>' appeared literally."""
        text = test_observed_text({
            "id": "H-03",
            "observed_value": {"active_workers": 0, "expected_workers": 0,
                               "planned_out": 0, "unplanned_missing": 0}})
        self.assertNotIn("&lt;", str(text))

    def test_every_test_id_produces_something(self):
        """A missing branch would render an empty line under the test name."""
        for test_id in ("H-01", "H-02", "H-03", "H-04", "H-05",
                        "H-06", "H-07", "H-08", "H-09"):
            rendered = str(test_observed_text({"id": test_id, "observed_value": None}))
            self.assertTrue(rendered.strip(), "{} rendered nothing".format(test_id))

    def test_unknown_test_id_does_not_crash(self):
        self.assertIsNotNone(test_observed_text({"id": "H-99", "observed_value": 1}))


class HealthViewTest(unittest.TestCase):
    def test_each_test_gains_observed_text(self):
        view = health_view(health_envelope(tests=[
            {"id": "H-01", "name": "Coordinator responsiveness", "state": "GOOD"}]))
        self.assertIn("observed_text", view["tests"][0])

    def test_original_payload_is_not_mutated(self):
        envelope = health_envelope(tests=[{"id": "H-01", "state": "GOOD"}])
        health_view(envelope)
        self.assertNotIn("observed_text", envelope["data"]["tests"][0])

    def test_empty_health_does_not_crash(self):
        self.assertEqual([], health_view({})["tests"])


class QueryChipsTest(unittest.TestCase):
    SUMMARY = {"total": 10, "running": 7, "queued": 2, "long_running": 3}

    def test_counts_come_from_the_summary(self):
        chips = query_chips(self.SUMMARY, {}, None, False)
        self.assertEqual([10, 7, 2, 3], [c["count"] for c in chips])

    def test_all_is_active_when_nothing_is_filtered(self):
        chips = query_chips(self.SUMMARY, {}, None, False)
        self.assertTrue(chips[0]["active"])
        self.assertFalse(any(c["active"] for c in chips[1:]))

    def test_long_running_chip_alerts_only_when_nonzero(self):
        self.assertTrue(query_chips(self.SUMMARY, {}, None, False)[3]["alert"])
        quiet = dict(self.SUMMARY, long_running=0)
        self.assertFalse(query_chips(quiet, {}, None, False)[3]["alert"])

    def test_existing_filters_are_preserved_in_chip_links(self):
        """Clicking a state chip must not silently drop the user filter."""
        chips = query_chips(self.SUMMARY, {"user": "analyst"}, None, False)
        self.assertIn("user=analyst", chips[1]["href"])

    def test_state_and_long_running_are_mutually_exclusive_in_links(self):
        chips = query_chips(self.SUMMARY, {}, "running", False)
        self.assertNotIn("long_running", chips[1]["href"])
        self.assertNotIn("state=", chips[3]["href"])

    def test_missing_summary_keys_default_to_zero(self):
        self.assertEqual([0, 0, 0, 0], [c["count"] for c in query_chips({}, {}, None, False)])


class AuditChipsTest(unittest.TestCase):
    def test_health_chip_sums_both_toggle_actions(self):
        chips = audit_chips(None, {"all": 9, "HEALTH_TEST_TOGGLE": 2,
                                   "HEALTH_ROLLUP_TOGGLE": 3})
        self.assertEqual(5, chips[2]["count"])

    def test_active_chip_follows_the_filter(self):
        chips = audit_chips("QUERY_KILL", {"all": 1, "QUERY_KILL": 1})
        self.assertTrue(chips[1]["active"])
        self.assertFalse(chips[0]["active"])

    def test_missing_counts_default_to_zero(self):
        self.assertEqual([0, 0, 0, 0], [c["count"] for c in audit_chips(None, {})])


class ExpandStateFilterTest(unittest.TestCase):
    def test_groups_expand_to_trino_states(self):
        self.assertEqual(["RUNNING", "FINISHING"], expand_state_filter("running"))
        self.assertIn("WAITING_FOR_RESOURCES", expand_state_filter("queued"))

    def test_no_filter_returns_none(self):
        self.assertIsNone(expand_state_filter(None))
        self.assertIsNone(expand_state_filter(""))

    def test_unknown_group_returns_none_rather_than_an_empty_filter(self):
        """An empty list would filter everything out and show a blank table."""
        self.assertIsNone(expand_state_filter("bogus"))


if __name__ == "__main__":
    unittest.main()


class OrderGroupsTest(unittest.TestCase):
    """FR-WL-06. Ranking is a different view, not a reordered tree."""

    TREE = [{"id": "global", "depth": 0, "children": [
        {"id": "global.adhoc", "depth": 1, "children": []},
        {"id": "global.etl", "depth": 1, "children": []},
    ]}]
    GROUPS = [{"id": "global", "cpu_ms": 5}, {"id": "global.adhoc", "cpu_ms": 100},
              {"id": "global.etl", "cpu_ms": None}]

    def test_the_default_is_the_tree(self):
        rows, ranked = views.order_groups(self.TREE, self.GROUPS)
        self.assertFalse(ranked)
        self.assertEqual(["global", "global.adhoc", "global.etl"],
                         [r["id"] for r in rows])

    def test_ranking_flattens_and_says_so(self):
        """Indentation claims "this group is inside that one". Once rows are
        reordered by CPU that is no longer true, so the caller is told to stop
        drawing the hierarchy rather than drawing a false one."""
        rows, ranked = views.order_groups(self.TREE, self.GROUPS, "cpu_ms")
        self.assertTrue(ranked)
        self.assertEqual(["global.adhoc", "global", "global.etl"],
                         [r["id"] for r in rows])

    def test_a_missing_value_ranks_last_not_as_zero(self):
        """A group with no CPU reading is unknown, not idle - ranking it as the
        least busy would be an assertion TMS cannot make."""
        rows, _ = views.order_groups(self.TREE, self.GROUPS, "cpu_ms", descending=True)
        self.assertEqual("global.etl", rows[-1]["id"])
        rows, _ = views.order_groups(self.TREE, self.GROUPS, "cpu_ms", descending=False)
        self.assertEqual("global.etl", rows[-1]["id"],
                         "still last when ascending; it is absent, not small")

    def test_an_unknown_sort_key_falls_back_to_the_tree(self):
        """The key comes from a query string. Anything not in the whitelist is
        ignored rather than reaching a sort over arbitrary attributes."""
        for key in ("", "secret", "__class__", "id"):
            _rows, ranked = views.order_groups(self.TREE, self.GROUPS, key)
            self.assertFalse(ranked, key)

    def test_every_sortable_column_has_a_label(self):
        for column in views.WORKLOAD_COLUMNS:
            self.assertTrue(views.column_label(column["key"]))
        self.assertEqual("", views.column_label("nope"))
