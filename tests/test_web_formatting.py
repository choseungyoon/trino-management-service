"""Tests for the Jinja filters.

Every number an operator reads passes through here. A filter that renders 0 as
a dash, or a dash as 0, changes what someone believes about a production
cluster - so the cases that matter most are the boundaries and the "no data"
paths, not the happy middle.

These were at 16% coverage while the UI was already in production.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.web.formatting import (  # noqa: E402
    EM_DASH,
    clock,
    data_size,
    duration,
    integer,
    parse_iso,
    percent,
    relative_time,
    resource_group,
    status_class,
    truncate,
)

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


class DurationTest(unittest.TestCase):
    def test_scale_boundaries(self):
        self.assertEqual("940ms", duration(940))
        self.assertEqual("1.0s", duration(1000))
        self.assertEqual("59.9s", duration(59_900))
        self.assertEqual("1m 00s", duration(60_000))
        self.assertEqual("59m 59s", duration(3_599_000))
        self.assertEqual("1h 00m", duration(3_600_000))
        self.assertEqual("23h 59m", duration(86_340_000))
        self.assertEqual("1d 00h", duration(86_400_000))

    def test_zero_is_a_real_value_not_a_dash(self):
        """0ms means "instant", not "unknown". Rendering it as - would lie."""
        self.assertEqual("0ms", duration(0))

    def test_missing_and_nonsense_render_as_dash(self):
        for value in (None, "abc", [], {}, -1):
            self.assertEqual(EM_DASH, duration(value), repr(value))

    def test_booleans_are_not_numbers(self):
        """True == 1 in Python; rendering it as 1ms would be nonsense."""
        self.assertEqual(EM_DASH, duration(True))


class DataSizeTest(unittest.TestCase):
    def test_units_step_at_1024(self):
        self.assertEqual("0 B", data_size(0))
        self.assertEqual("1023 B", data_size(1023))
        self.assertEqual("1.0 KB", data_size(1024))
        self.assertEqual("1.0 MB", data_size(1024 ** 2))
        self.assertEqual("1.0 GB", data_size(1024 ** 3))
        self.assertEqual("1.0 TB", data_size(1024 ** 4))

    def test_missing_renders_as_dash(self):
        self.assertEqual(EM_DASH, data_size(None))
        self.assertEqual(EM_DASH, data_size(-1))

    def test_very_large_values_stay_in_the_top_unit(self):
        """Must not fall off the end of the unit table."""
        self.assertTrue(data_size(1024 ** 9).endswith(("PB", "EB", "TB")))


class PercentAndIntegerTest(unittest.TestCase):
    def test_percent(self):
        self.assertEqual("58.7%", percent(58.7))
        self.assertEqual("0.0%", percent(0))
        self.assertEqual("59%", percent(58.7, digits=0))
        self.assertEqual(EM_DASH, percent(None))

    def test_integer_uses_thousands_separators(self):
        self.assertEqual("1,234,567", integer(1234567))
        self.assertEqual("0", integer(0))
        self.assertEqual(EM_DASH, integer(None))


class ParseIsoTest(unittest.TestCase):
    def test_trailing_z_is_accepted(self):
        """Python 3.9's fromisoformat rejects Z, and both Trino and PostgreSQL
        emit it. This is the reason the helper exists."""
        parsed = parse_iso("2026-08-08T12:00:00Z")
        self.assertEqual(datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc), parsed)

    def test_naive_timestamps_are_assumed_utc(self):
        parsed = parse_iso("2026-08-08T12:00:00")
        self.assertEqual(timezone.utc, parsed.tzinfo)

    def test_offset_is_preserved(self):
        parsed = parse_iso("2026-08-08T12:00:00+09:00")
        self.assertEqual(timedelta(hours=9), parsed.utcoffset())

    def test_garbage_returns_none_rather_than_raising(self):
        for value in (None, "", "not-a-date", 12345, []):
            self.assertIsNone(parse_iso(value), repr(value))


class RelativeTimeTest(unittest.TestCase):
    """The freshness label. If this lies, stale data looks current."""

    def test_scale(self):
        cases = [
            (timedelta(seconds=3), "3s ago"),
            (timedelta(seconds=59), "59s ago"),
            (timedelta(minutes=1), "1m ago"),
            (timedelta(minutes=59), "59m ago"),
            (timedelta(hours=1), "1h ago"),
            (timedelta(hours=23), "23h ago"),
            (timedelta(days=1), "1d ago"),
        ]
        for delta, expected in cases:
            self.assertEqual(expected, relative_time(NOW - delta, now=NOW), str(delta))

    def test_no_timestamp_is_never_not_zero_seconds(self):
        """"0s ago" on missing data would read as perfectly fresh."""
        self.assertEqual("never", relative_time(None, now=NOW))

    def test_clock_skew_does_not_produce_negative_ages(self):
        """A collector slightly ahead of the API host must not render '-3s ago'."""
        self.assertEqual("just now", relative_time(NOW + timedelta(seconds=3), now=NOW))


class ResourceGroupTest(unittest.TestCase):
    def test_path_array_is_joined(self):
        """Trino reports resource groups as an array, verified @477."""
        self.assertEqual("global.adhoc.dashboard",
                         resource_group(["global", "adhoc", "dashboard"]))

    def test_string_passes_through(self):
        self.assertEqual("global", resource_group("global"))

    def test_empty_and_missing_render_as_dash(self):
        for value in (None, [], "", {}):
            self.assertEqual(EM_DASH, resource_group(value), repr(value))


class StatusClassTest(unittest.TestCase):
    def test_health_states(self):
        self.assertEqual("good", status_class("GOOD"))
        self.assertEqual("concerning", status_class("CONCERNING"))
        self.assertEqual("bad", status_class("BAD"))
        self.assertEqual("unknown", status_class("UNKNOWN"))

    def test_query_states_collapse_into_running_or_queued(self):
        for state in ("RUNNING", "FINISHING"):
            self.assertEqual("running", status_class(state), state)
        for state in ("QUEUED", "WAITING_FOR_RESOURCES", "PLANNING",
                      "STARTING", "DISPATCHING"):
            self.assertEqual("queued", status_class(state), state)

    def test_unrecognised_state_is_unknown_never_good(self):
        """A state we do not know must never render green."""
        for value in ("FINISHED", "FAILED", "", None, 42, "nonsense"):
            self.assertEqual("unknown", status_class(value), repr(value))


class TruncateTest(unittest.TestCase):
    def test_whitespace_is_collapsed(self):
        self.assertEqual("SELECT 1 FROM t", truncate("SELECT   1\n  FROM\tt"))

    def test_long_text_is_cut_with_an_ellipsis(self):
        result = truncate("x" * 500, limit=50)
        self.assertEqual(50, len(result))
        self.assertTrue(result.endswith("…"))

    def test_text_at_the_limit_is_untouched(self):
        self.assertEqual("y" * 50, truncate("y" * 50, limit=50))

    def test_non_string_yields_empty_string(self):
        for value in (None, 42, []):
            self.assertEqual("", truncate(value), repr(value))


class ClockTest(unittest.TestCase):
    def test_missing_renders_as_dash(self):
        self.assertEqual(EM_DASH, clock(None))
        self.assertEqual(EM_DASH, clock("garbage"))

    def test_renders_a_wall_clock_string(self):
        rendered = clock("2026-08-08T12:00:00Z")
        self.assertRegex(rendered, r"^[A-Z][a-z]{2} \d{2} \d{2}:\d{2}:\d{2}$")


if __name__ == "__main__":
    unittest.main()
