"""Chart geometry, computed on the server.

Arithmetic that decides what a reader believes, so it is tested rather than
eyeballed in a browser.
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from tms.web.chart import DASHES, SERIES_DARK, SERIES_LIGHT, line_chart, summarise  # noqa: E402


def series(name, *values):
    return {"name": name,
            "points": [{"x_label": str(i), "y": v} for i, v in enumerate(values)]}


class LineChartTest(unittest.TestCase):
    def test_a_single_point_per_series_is_not_a_chart(self):
        """⛔ Two clusters measured once each is two dots and no line.

        Drawing it would be a chart pretending to be a trend; the summary
        numbers beside it say the same thing without the pretence.
        """
        self.assertIsNone(line_chart([series("a", 100), series("b", 120)]))

    def test_nothing_at_all_is_not_a_chart(self):
        self.assertIsNone(line_chart([]))
        self.assertIsNone(line_chart([{"name": "a", "points": []}]))

    def test_the_axis_starts_at_zero(self):
        """A y-axis that starts at the lowest sample turns 3% into a cliff."""
        chart = line_chart([series("a", 300, 310)])
        self.assertEqual(0.0, chart["ticks"][0]["value"])
        self.assertGreaterEqual(chart["top"], 310)

    def test_the_axis_top_is_a_round_number(self):
        for values, expected in (((310, 353), 500.0), ((30, 44), 50.0),
                                 ((1, 2), 2.5), ((4100, 4300), 5000.0)):
            self.assertEqual(expected, line_chart([series("a", *values)])["top"],
                             msg=str(values))

    def test_points_are_ordered_left_to_right_as_given(self):
        chart = line_chart([series("a", 100, 200, 300)])
        xs = [p["x"] for p in chart["series"][0]["points"]]
        self.assertEqual(xs, sorted(xs))

    def test_a_higher_value_is_drawn_higher(self):
        """Screen y grows downward; a bigger number must sit nearer the top."""
        chart = line_chart([series("a", 100, 400)])
        low, high = chart["series"][0]["points"]
        self.assertGreater(low["y"], high["y"])

    def test_series_take_fixed_slots_and_are_never_cycled_silently(self):
        chart = line_chart([series(n, 1, 2) for n in "abcde"])
        slots = [s["slot"] for s in chart["series"]]
        self.assertEqual([0, 1, 2, 3, 0], slots)
        # The fifth reuses a colour, so it carries a dash as well - identity
        # never rests on colour alone.
        self.assertEqual("", chart["series"][0]["dash"])
        self.assertEqual(DASHES[1], chart["series"][4]["dash"])

    def test_only_three_x_labels_are_drawn(self):
        """One under every point is unreadable at ten samples, worse at fifty."""
        chart = line_chart([series("a", *range(1, 21))])
        self.assertEqual(3, len(chart["x_labels"]))

    def test_a_missing_value_does_not_become_zero(self):
        entry = {"name": "a", "points": [{"x_label": "1", "y": 100},
                                         {"x_label": "2", "y": None},
                                         {"x_label": "3", "y": 300}]}
        chart = line_chart([entry])
        self.assertEqual([100, 300],
                         [p["value"] for p in chart["series"][0]["points"]])

    def test_both_ramps_have_the_same_number_of_slots(self):
        """A theme with fewer steps would repaint series on a theme switch."""
        self.assertEqual(len(SERIES_LIGHT), len(SERIES_DARK))


class SummaryTest(unittest.TestCase):
    def test_average_and_median_are_both_reported(self):
        """The gap between them is the reading: a mean above the median
        means a tail, which either number alone would hide."""
        stats = summarise([100, 100, 100, 400])
        self.assertEqual(175.0, stats["avg"])
        self.assertEqual(100.0, stats["median"])

    def test_an_empty_series_reports_nothing_rather_than_zero(self):
        stats = summarise([])
        self.assertEqual(0, stats["count"])
        self.assertIsNone(stats["avg"])
        self.assertIsNone(stats["median"])

    def test_none_values_are_skipped_not_counted(self):
        stats = summarise([100, None, 200])
        self.assertEqual(2, stats["count"])
        self.assertEqual(150.0, stats["avg"])


if __name__ == "__main__":
    unittest.main()
