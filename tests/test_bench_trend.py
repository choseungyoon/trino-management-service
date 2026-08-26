"""Benchmark results aggregated for a chart.

Numbers, not pixels. What is checked is the judgement: which runs group
together, what the middle of a run is, and whether there is enough to call a
trend at all.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from tms.bench.trend import build, summarise  # noqa: E402

NOW = datetime(2026, 8, 27, 3, 0, tzinfo=timezone.utc)


def row(run_id, cluster, elapsed, day=0, state="SUCCEEDED"):
    return {"run_id": run_id, "cluster": cluster, "elapsed_ms": elapsed,
            "state": state, "run_started_at": NOW + timedelta(days=day)}


class TrendTest(unittest.TestCase):
    def test_each_point_is_a_run_not_an_execution(self):
        """⛔ Plotting every repetition draws the warm-up as a spike on every
        run and buries the thing the chart is for."""
        result = build([row(1, "prod-a", 400), row(1, "prod-a", 300),
                        row(1, "prod-a", 302)])
        points = result["series"][0]["points"]
        self.assertEqual(1, len(points))
        self.assertEqual(302, points[0]["median_ms"])
        self.assertEqual(3, points[0]["repetitions"])

    def test_one_point_per_series_is_not_drawable(self):
        """Two clusters measured once each is two dots and no line."""
        result = build([row(1, "prod-a", 100), row(2, "prod-b", 120)])
        self.assertFalse(result["drawable"])
        self.assertEqual(2, len(result["summaries"]))

    def test_two_runs_on_one_cluster_are_drawable(self):
        result = build([row(1, "prod-a", 100), row(2, "prod-a", 120, day=1)])
        self.assertTrue(result["drawable"])

    def test_points_run_oldest_first(self):
        """A chart of time that runs right-to-left is a trap."""
        result = build([row(2, "prod-a", 120, day=1), row(1, "prod-a", 100)])
        ats = [p["at"] for p in result["series"][0]["points"]]
        self.assertEqual(ats, sorted(ats))

    def test_failures_are_left_out_of_the_line(self):
        """A query that failed fast would otherwise look like the fastest."""
        result = build([row(1, "prod-a", 100), row(1, "prod-a", 5, state="FAILED"),
                        row(2, "prod-a", 110, day=1)])
        self.assertEqual([100, 110],
                         [p["median_ms"] for p in result["series"][0]["points"]])

    def test_clusters_get_their_own_series(self):
        result = build([row(1, "prod-a", 100), row(2, "prod-b", 200)])
        self.assertEqual(["prod-a", "prod-b"],
                         [s["cluster"] for s in result["series"]])

    def test_nothing_at_all_is_not_drawable(self):
        result = build([])
        self.assertFalse(result["drawable"])
        self.assertEqual([], result["series"])


class SummariseTest(unittest.TestCase):
    def test_average_and_median_are_both_reported(self):
        """The gap between them is the reading: a mean above the median means
        a tail, which either number alone would hide."""
        stats = summarise([100, 100, 100, 400])
        self.assertEqual(175.0, stats["avg"])
        self.assertEqual(100.0, stats["median"])

    def test_an_empty_series_reports_nothing_rather_than_zero(self):
        stats = summarise([])
        self.assertEqual(0, stats["count"])
        self.assertIsNone(stats["avg"])

    def test_none_values_are_skipped_not_counted(self):
        stats = summarise([100, None, 200])
        self.assertEqual(2, stats["count"])
        self.assertEqual(150.0, stats["avg"])


if __name__ == "__main__":
    unittest.main()
