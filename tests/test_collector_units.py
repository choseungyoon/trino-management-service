"""Tests for airlift unit parsing.

Formats were read off the airlift source, not guessed:

* Duration serialises via `@JsonValue` on `toString()`: two decimals plus a
  suffix from {ns, us, ms, s, m, h, d}.
* DataSize serialises via `@JsonValue` on `toBytesValueString()`, which is
  *always* bytes with a "B" suffix - "8589934592B", never "8GB". Human-readable
  forms are accepted anyway so a future annotation change does not silently
  produce None everywhere.

A malformed value must return None rather than raise: one odd duration in one
query must not take down an entire poll.
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.collector.units import (  # noqa: E402
    parse_data_size_bytes,
    parse_duration_ms,
    truncate_utf8,
)


class ParseDurationTest(unittest.TestCase):
    def test_all_suffixes(self):
        cases = [
            ("1.00ns", 1e-6),
            ("1.00us", 1e-3),
            ("23.45ms", 23.45),
            ("1.98s", 1980.0),
            ("2.50m", 150_000.0),
            ("1.00h", 3_600_000.0),
            ("1.00d", 86_400_000.0),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertAlmostEqual(parse_duration_ms(text), expected, places=6)

    def test_zero_and_integers(self):
        self.assertEqual(parse_duration_ms("0.00s"), 0.0)
        self.assertEqual(parse_duration_ms("5s"), 5000.0)

    def test_numeric_input_is_treated_as_milliseconds(self):
        self.assertEqual(parse_duration_ms(1500), 1500.0)

    def test_malformed_values_return_none(self):
        for value in ("", "abc", "1.0x", None, [], {}, True, "s", "1.0 seconds"):
            with self.subTest(value=value):
                self.assertIsNone(parse_duration_ms(value))


class ParseDataSizeTest(unittest.TestCase):
    def test_byte_string_is_the_real_wire_format(self):
        self.assertEqual(parse_data_size_bytes("8589934592B"), 8589934592)
        self.assertEqual(parse_data_size_bytes("0B"), 0)

    def test_human_readable_units_are_also_accepted(self):
        self.assertEqual(parse_data_size_bytes("1kB"), 1024)
        self.assertEqual(parse_data_size_bytes("8GB"), 8 * (1 << 30))
        self.assertEqual(parse_data_size_bytes("1.5MB"), int(1.5 * (1 << 20)))

    def test_malformed_values_return_none(self):
        for value in ("", "abc", "1.0Q", None, [], True, "GB"):
            with self.subTest(value=value):
                self.assertIsNone(parse_data_size_bytes(value))


class TruncateUtf8Test(unittest.TestCase):
    def test_short_text_is_untouched(self):
        text, truncated = truncate_utf8("SELECT 1", 100)
        self.assertEqual(text, "SELECT 1")
        self.assertFalse(truncated)

    def test_budget_is_in_bytes_not_characters(self):
        text, truncated = truncate_utf8("S" * 200, 100)
        self.assertTrue(truncated)
        self.assertEqual(len(text.encode("utf-8")), 100)

    def test_multibyte_characters_are_never_split(self):
        """SQL carries Korean in comments and literals; a split codepoint would
        corrupt the preview or raise on decode."""
        source = "한글" * 100
        for budget in range(1, 40):
            with self.subTest(budget=budget):
                text, _ = truncate_utf8(source, budget)
                encoded = text.encode("utf-8")
                self.assertLessEqual(len(encoded), budget)
                self.assertEqual(encoded.decode("utf-8"), text)

    def test_zero_budget(self):
        text, truncated = truncate_utf8("SELECT 1", 0)
        self.assertEqual(text, "")
        self.assertTrue(truncated)

    def test_empty_input(self):
        self.assertEqual(truncate_utf8("", 100), ("", False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
