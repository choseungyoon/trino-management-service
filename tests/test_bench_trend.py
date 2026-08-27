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

from tms.bench.trend import BY_DAY, BY_MONTH, BY_RUN, build, summarise  # noqa: E402

NOW = datetime(2026, 8, 27, 3, 0, tzinfo=timezone.utc)


def row(run_id, cluster, elapsed, day=0, state="SUCCEEDED", hours=0):
    return {"run_id": run_id, "cluster": cluster, "elapsed_ms": elapsed,
            "state": state,
            "run_started_at": NOW + timedelta(days=day, hours=hours)}


class TrendTest(unittest.TestCase):
    def test_each_point_is_a_run_not_an_execution(self):
        """⛔ Plotting every repetition draws the warm-up as a spike on every
        run and buries the thing the chart is for."""
        result = build([row(1, "prod-a", 400), row(1, "prod-a", 300),
                        row(1, "prod-a", 302)])
        points = result["series"][0]["points"]
        self.assertEqual(1, len(points))
        self.assertEqual(302, points[0]["median_ms"])
        self.assertEqual(3, points[0]["executions"])
        self.assertEqual(1, points[0]["runs"])

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


class BucketTest(unittest.TestCase):
    """Day and month grouping. A benchmark can run several times a day, and at
    that point one dot per run is noise rather than a trend."""

    def test_runs_on_the_same_day_collapse_to_one_point(self):
        history = [row(1, "prod-a", 100, hours=1), row(2, "prod-a", 300, hours=5),
                   row(3, "prod-a", 200, day=1)]
        daily = build(history, bucket=BY_DAY)
        points = daily["series"][0]["points"]

        self.assertEqual(2, len(points), "two calendar days")
        # Median of the day's executions, not of the runs' medians: the
        # bucket is a bag of measurements, same as a run is.
        self.assertEqual(200, points[0]["median_ms"])
        self.assertEqual(2, points[0]["runs"])
        self.assertEqual(2, points[0]["executions"])
        self.assertEqual("Daily", daily["bucket_label"])

    def test_every_run_is_its_own_point_by_default(self):
        history = [row(1, "prod-a", 100, hours=1), row(2, "prod-a", 300, hours=5)]
        self.assertEqual(2, len(build(history)["series"][0]["points"]))
        self.assertEqual(BY_RUN, build(history)["bucket"])

    def test_a_month_folds_its_days(self):
        history = [row(1, "prod-a", 100), row(2, "prod-a", 300, day=2),
                   row(3, "prod-a", 900, day=40)]
        monthly = build(history, bucket=BY_MONTH)
        points = monthly["series"][0]["points"]
        self.assertEqual(2, len(points), "August and October")
        self.assertEqual(2, points[0]["runs"])

    def test_an_unknown_bucket_falls_back_to_runs(self):
        """A query string is user input. It must not decide nothing at all."""
        self.assertEqual(BY_RUN, build([row(1, "a", 100)], bucket="fortnight")["bucket"])


class SharedAxisTest(unittest.TestCase):
    """⛔ Series used to be indexed by position within themselves, so a cluster
    with four runs and one with two were drawn across the same width - the
    short one's last point sat under the long one's second, and the chart said
    they were measured at the same time."""

    def test_two_clusters_share_one_axis(self):
        history = [row(1, "prod-a", 100, day=0), row(2, "prod-a", 110, day=1),
                   row(3, "prod-a", 120, day=2), row(4, "prod-b", 900, day=2)]
        result = build(history, bucket=BY_DAY)
        by_cluster = {s["cluster"]: s for s in result["series"]}

        self.assertEqual(3, len(result["buckets"]))
        self.assertEqual([0, 1, 2], [p["x"] for p in by_cluster["prod-a"]["points"]])
        # prod-b was only measured on the last day. It belongs at the end of
        # the axis, not at the start of its own.
        self.assertEqual([2], [p["x"] for p in by_cluster["prod-b"]["points"]])

    def test_the_axis_runs_oldest_first(self):
        result = build([row(2, "a", 100, day=5), row(1, "a", 100, day=0)],
                       bucket=BY_DAY)
        moments = [b["at"] for b in result["buckets"]]
        self.assertEqual(sorted(moments), moments)


class MeanLineTest(unittest.TestCase):
    def test_the_reference_line_averages_what_is_drawn(self):
        """⛔ Not every execution. A line computed from a different population
        than the dots is a line that lies about them."""
        history = [row(1, "prod-a", 100), row(1, "prod-a", 100), row(1, "prod-a", 400),
                   row(2, "prod-a", 200, day=1)]
        series = build(history)["series"][0]

        self.assertEqual([100, 200], [p["median_ms"] for p in series["points"]])
        self.assertEqual(150, series["mean_of_points"])
        # The summary still covers every execution, and the two differ. That
        # gap is the finding, so both are reported.
        summary = build(history)["summaries"][0]
        self.assertEqual(200, summary["avg"])
        self.assertEqual(4, summary["count"])

    def test_a_series_with_no_points_has_no_line(self):
        self.assertEqual([], build([row(1, "a", 100, state="FAILED")])["series"])
