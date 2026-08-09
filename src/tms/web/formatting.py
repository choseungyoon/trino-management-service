"""Display formatting for the web UI.

Kept out of the templates so the rules are testable: an operator reading
"21m 34s" during an incident must never see "1294000.0" because a value
arrived as a string, and a missing value must render as an em dash rather
than "None".

Every function here is total — it takes anything and returns a string.
A malformed reading is a display problem, not a 500.

Python 3.9 compatible.
"""

from datetime import datetime, timezone
from typing import Any, Optional

EM_DASH = "—"

_BYTE_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def _as_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def duration(milliseconds: Any) -> str:
    """Human elapsed time. 21m 34s, 4h 12m, 940ms."""
    ms = _as_number(milliseconds)
    if ms is None or ms < 0:
        return EM_DASH
    if ms < 1000:
        return "{:.0f}ms".format(ms)
    seconds = ms / 1000.0
    if seconds < 60:
        return "{:.1f}s".format(seconds)
    minutes, sec = divmod(int(seconds), 60)
    if minutes < 60:
        return "{}m {:02d}s".format(minutes, sec)
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return "{}h {:02d}m".format(hours, minutes)
    days, hours = divmod(hours, 24)
    return "{}d {:02d}h".format(days, hours)


def data_size(num_bytes: Any) -> str:
    """Binary-prefixed size. Trino reports raw bytes; operators think in GB."""
    value = _as_number(num_bytes)
    if value is None or value < 0:
        return EM_DASH
    unit = 0
    while value >= 1024 and unit < len(_BYTE_UNITS) - 1:
        value /= 1024.0
        unit += 1
    if unit == 0:
        return "{:.0f} B".format(value)
    return "{:.1f} {}".format(value, _BYTE_UNITS[unit])


def percent(value: Any, digits: int = 1) -> str:
    number = _as_number(value)
    if number is None:
        return EM_DASH
    return "{:.{d}f}%".format(number, d=digits)


def integer(value: Any) -> str:
    number = _as_number(value)
    if number is None:
        return EM_DASH
    return "{:,}".format(int(number))


def parse_iso(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    # Python 3.9's fromisoformat rejects the trailing Z that Trino and
    # PostgreSQL both emit.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def relative_time(value: Any, now: Optional[datetime] = None) -> str:
    """"3s ago". The freshness label the whole UI hangs on."""
    moment = parse_iso(value)
    if moment is None:
        return "never"
    now = now or datetime.now(timezone.utc)
    seconds = (now - moment).total_seconds()
    if seconds < 0:
        return "just now"
    if seconds < 60:
        return "{:.0f}s ago".format(seconds)
    if seconds < 3600:
        return "{:.0f}m ago".format(seconds // 60)
    if seconds < 86400:
        return "{:.0f}h ago".format(seconds // 3600)
    return "{:.0f}d ago".format(seconds // 86400)


def clock(value: Any) -> str:
    """Wall-clock time for audit and event rows."""
    moment = parse_iso(value)
    if moment is None:
        return EM_DASH
    return moment.astimezone().strftime("%b %d %H:%M:%S")


def time_only(value: Any) -> str:
    """Wall-clock time with no date.

    For the restart progress log, where every line happens within the same few
    minutes: the repeated date says nothing and wraps the column onto a second
    row, halving the density of a log the operator is reading live.
    """
    moment = parse_iso(value)
    if moment is None:
        return EM_DASH
    return moment.astimezone().strftime("%H:%M:%S")


def resource_group(value: Any) -> str:
    """Trino reports resource groups as a path array."""
    if isinstance(value, (list, tuple)) and value:
        return ".".join(str(part) for part in value)
    if isinstance(value, str) and value:
        return value
    return EM_DASH


def status_class(state: Any) -> str:
    """Map a backend state to its CSS modifier."""
    return {
        "GOOD": "good",
        "CONCERNING": "concerning",
        "BAD": "bad",
        "UNKNOWN": "unknown",
        "RUNNING": "running",
        "FINISHING": "running",
        "QUEUED": "queued",
        "WAITING_FOR_RESOURCES": "queued",
        "PLANNING": "queued",
        "STARTING": "queued",
        "DISPATCHING": "queued",
    }.get(str(state).upper(), "unknown")


def truncate(text: Any, limit: int = 120) -> str:
    if not isinstance(text, str):
        return ""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "…"


FILTERS = {
    "duration": duration,
    "data_size": data_size,
    "percent": percent,
    "integer": integer,
    "relative_time": relative_time,
    "clock": clock,
    "time_only": time_only,
    "resource_group": resource_group,
    "status_class": status_class,
    "truncate_sql": truncate,
}
