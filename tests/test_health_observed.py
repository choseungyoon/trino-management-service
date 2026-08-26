"""The sentence under a health test's name.

Moved out of the view layer when the console became a client: which tests
exist and what their numbers mean is server knowledge, and a client that has
not heard of H-03 must not be the thing deciding how H-03 reads.
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from tms.health.observed import observed_segments  # noqa: E402


def flat(test):
    return "".join(part["text"] for part in observed_segments(test))


class ObservedSegmentsTest(unittest.TestCase):
    def test_h03_keeps_planned_and_unplanned_apart(self):
        """⛔ The whole point of that test.

        A worker draining on purpose and one that vanished are different
        facts. Collapsing them loses the only thing worth reading.
        """
        text = flat({"id": "H-03", "observed_value": {
            "active_workers": 10, "expected_workers": 12,
            "planned_out": 1, "unplanned_missing": 1}})
        self.assertIn("10 of 12 workers active", text)
        self.assertIn("1 draining (planned)", text)
        self.assertIn("1 missing unplanned", text)

    def test_h03_says_nothing_about_draining_when_none_is(self):
        text = flat({"id": "H-03", "observed_value": {
            "active_workers": 12, "expected_workers": 12,
            "planned_out": 0, "unplanned_missing": 0}})
        self.assertEqual("12 of 12 workers active", text)

    def test_the_numbers_are_the_emphasised_part(self):
        """The eye should land on the count, not on the word 'workers'."""
        parts = observed_segments({"id": "H-05", "observed_value": 7.5})
        strong = [p["text"] for p in parts if p["strong"]]
        self.assertEqual(["7.5%"], strong)

    def test_h04_carries_its_threshold(self):
        text = flat({"id": "H-04", "observed_value": 82, "threshold": 85})
        self.assertIn("82%", text)
        self.assertIn("threshold 85%", text)

    def test_h07_distinguishes_a_baseline_from_a_new_kill(self):
        """The first reading is not "zero kills", it is "nothing to compare"."""
        self.assertIn("baseline recorded",
                     flat({"id": "H-07", "observed_value": {"total": 4}}))
        self.assertIn("2 new OOM kills",
                     flat({"id": "H-07",
                           "observed_value": {"delta": 2, "total": 6}}))

    def test_an_unknown_shape_still_reads_as_words(self):
        """⛔ Never a Python repr in front of an operator mid-incident."""
        text = flat({"id": "H-99", "observed_value": {"queued": 3, "running": 8}})
        self.assertNotIn("{", text)
        self.assertIn("queued 3", text)
        self.assertIn("running 8", text)

    def test_no_reading_says_so(self):
        self.assertEqual("no reading", flat({"id": "H-01", "observed_value": None}))

    def test_segments_never_carry_markup(self):
        """HTML inside a JSON payload is a habit that ends badly."""
        for test in ({"id": "H-03", "observed_value": {"active_workers": 1,
                                                       "expected_workers": 2,
                                                       "planned_out": 1,
                                                       "unplanned_missing": 1}},
                     {"id": "H-05", "observed_value": 1.0},
                     {"id": "H-99", "observed_value": {"a": 1}}):
            for part in observed_segments(test):
                self.assertNotIn("<", part["text"])


if __name__ == "__main__":
    unittest.main()


class BottleneckTextTest(unittest.TestCase):
    """The diagnosis vocabulary, kept beside the diagnosis.

    Moved out of the view layer with the rest: adding a reason should not need
    a frontend release to become readable.
    """

    def test_each_code_reads_as_a_sentence(self):
        from tms.collector.resourcegroups import (
            CONCURRENCY_CAPPED,
            REJECTING,
            bottleneck_text,
        )

        self.assertEqual("Queue full — new queries rejected", bottleneck_text(REJECTING))
        self.assertEqual("At concurrency limit", bottleneck_text(CONCURRENCY_CAPPED))

    def test_an_unknown_code_renders_as_itself_not_as_blank(self):
        """⛔ A blank cell beside a highlighted row reads as "no problem",
        which is the opposite of what happened."""
        from tms.collector.resourcegroups import bottleneck_text

        self.assertEqual("something_new", bottleneck_text("something_new"))

    def test_no_bottleneck_is_empty(self):
        from tms.collector.resourcegroups import bottleneck_text

        self.assertEqual("", bottleneck_text(None))

    def test_a_summarised_group_carries_its_sentence(self):
        from tms.collector.resourcegroups import summarise_group

        row = summarise_group(["global"], {
            "RunningQueries": 4, "QueuedQueries": 3,
            "HardConcurrencyLimit": 4, "MaxQueuedQueries": 100})
        self.assertEqual("concurrency_limit", row["bottleneck"])
        self.assertEqual("At concurrency limit", row["bottleneck_text"])
