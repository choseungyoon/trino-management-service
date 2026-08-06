"""Parsers for airlift unit types as they appear in Trino JSON.

Trino serialises `io.airlift.units.Duration` and `DataSize` as strings, not
numbers, so every duration and size in `/v1/query` has to be parsed. Both
formats were read off the airlift source rather than guessed:

* Duration  - `@JsonValue` on `toString()`: magnitude with two decimals plus a
  suffix from {ns, us, ms, s, m, h, d}. Examples: "1.98s", "23.45ms", "2.50m".
* DataSize  - `@JsonValue` on `toBytesValueString()`, which is *always* bytes
  with a "B" suffix regardless of magnitude. Example: "8589934592B", never
  "8GB". Human-readable forms are still accepted here in case a future release
  changes the annotation.

Parsers return None on anything unexpected. A malformed duration must not take
down a poll: the field is dropped and the rest of the snapshot survives.

Python 3.9 compatible.
"""

import re
from typing import Optional

_DURATION_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(ns|us|ms|s|m|h|d)\s*$")
_DATASIZE_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(B|kB|MB|GB|TB|PB|EB)\s*$")

_DURATION_TO_MS = {
    "ns": 1e-6,
    "us": 1e-3,
    "ms": 1.0,
    "s": 1000.0,
    "m": 60_000.0,
    "h": 3_600_000.0,
    "d": 86_400_000.0,
}

_DATASIZE_TO_BYTES = {
    "B": 1,
    "kB": 1 << 10,
    "MB": 1 << 20,
    "GB": 1 << 30,
    "TB": 1 << 40,
    "PB": 1 << 50,
    "EB": 1 << 60,
}


def parse_duration_ms(value: object) -> Optional[float]:
    """Return milliseconds, or None if the value cannot be understood."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        # Defensive: a future release could switch to numeric milliseconds.
        return float(value)
    if not isinstance(value, str):
        return None
    match = _DURATION_RE.match(value)
    if not match:
        return None
    magnitude, unit = match.groups()
    try:
        return float(magnitude) * _DURATION_TO_MS[unit]
    except (ValueError, KeyError):
        return None


def parse_data_size_bytes(value: object) -> Optional[int]:
    """Return bytes, or None if the value cannot be understood."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        return None
    match = _DATASIZE_RE.match(value)
    if not match:
        return None
    magnitude, unit = match.groups()
    try:
        return int(float(magnitude) * _DATASIZE_TO_BYTES[unit])
    except (ValueError, KeyError):
        return None


def truncate_utf8(text: str, max_bytes: int) -> tuple:
    """Cut `text` to at most `max_bytes` UTF-8 bytes without splitting a char.

    Returns (text, was_truncated). SQL is full of multi-byte characters in
    comments and string literals, so slicing by character count would either
    overshoot the budget or corrupt the output.
    """
    if max_bytes <= 0:
        return "", bool(text)
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    cut = encoded[:max_bytes]
    # Drop any partial trailing sequence.
    while cut and (cut[-1] & 0xC0) == 0x80:
        cut = cut[:-1]
    if cut and (cut[-1] & 0xC0) == 0xC0:
        cut = cut[:-1]
    return cut.decode("utf-8", errors="ignore"), True
