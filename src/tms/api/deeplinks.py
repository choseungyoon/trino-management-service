"""Deep-link builders (FR-LOG-DEEPLINK).

Pure functions with no knowledge of where their inputs came from. That is
deliberate: when the separate query-history project is merged (D-001), its
screens should be able to call these unchanged.

An unset template yields no link at all. A dead link is worse than an absent
one - it costs a click and some confidence to discover it goes nowhere.

Python 3.9 compatible.
"""

import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional


def _to_millis(moment: datetime) -> int:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def build_log_url(
    template: str,
    query_id: Optional[str] = None,
    node_host: Optional[str] = None,
    cluster: Optional[str] = None,
    time_from: Optional[datetime] = None,
    time_to: Optional[datetime] = None,
    padding_seconds: int = 300,
) -> Optional[str]:
    """Render a log-system URL from a configured template.

    The window is padded on both sides. Cutting exactly to the query's own
    interval reliably hides the log lines that explain it - the interesting
    entries sit just before the start and just after the end.
    """
    if not template:
        return None

    now = datetime.now(timezone.utc)
    start = (time_from or now) - timedelta(seconds=padding_seconds)
    end = (time_to or now) + timedelta(seconds=padding_seconds)

    terms = [term for term in (query_id, node_host, cluster) if term]
    substitutions: Dict[str, str] = {
        "query": urllib.parse.quote(" ".join(terms)),
        "query_id": urllib.parse.quote(query_id or ""),
        "node": urllib.parse.quote(node_host or ""),
        "cluster": urllib.parse.quote(cluster or ""),
        "from_ms": str(_to_millis(start)),
        "to_ms": str(_to_millis(end)),
        "from_iso": start.isoformat(),
        "to_iso": end.isoformat(),
    }
    try:
        return template.format(**substitutions)
    except (KeyError, IndexError, ValueError):
        # A malformed template is a configuration bug; it must not take down the
        # query list that merely wanted to attach a link.
        return None


def build_query_history_url(template: str, query_id: str) -> Optional[str]:
    """Link into the separate query-history project (D-001).

    This is the only R1 route from a live query to its completed record, so it
    is worth wiring as soon as the URL pattern is known.
    """
    if not template or not query_id:
        return None
    try:
        return template.format(query_id=urllib.parse.quote(query_id))
    except (KeyError, IndexError, ValueError):
        return None


def build_grafana_url(template: str, cluster: str) -> Optional[str]:
    if not template or not cluster:
        return None
    try:
        return template.format(cluster=urllib.parse.quote(cluster))
    except (KeyError, IndexError, ValueError):
        return None
