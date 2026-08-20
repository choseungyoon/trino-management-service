"""View-model builders: service output → template-ready shapes.

Kept separate from the routes so the shaping is testable without an HTTP layer,
and separate from the templates so the rules live in Python rather than in
Jinja expressions nobody can unit-test.

Two shaping rules carry real meaning:

* `test_observed_text` turns a health test's raw observation into the sentence
  an operator reads. H-03's dict of worker counts becomes "10 of 12 active ·
  1 draining (planned) · 1 missing unplanned" — the planned/unplanned split is
  the whole point of that test and must survive into the UI.
* `cluster_summary` reports `active_workers` as workers, not nodes. The backend
  counts the coordinator in ActiveNodeCount (verified: a 12-worker cluster
  reports 13); showing 13/12 would look like a bug.

Python 3.9 compatible.
"""

from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from markupsafe import Markup, escape

from tms.web.formatting import integer, percent


def _em(value: Any) -> Markup:
    """Emphasised, escaped value. Built with Markup rather than returning a raw
    string so the template can render it without `| safe` — an escape hatch that,
    once opened for one field, gets copied to fields carrying operator input."""
    return Markup("<b>{}</b>").format(value)


def _mono(value: Any) -> Markup:
    return Markup('<span class="mono num">{}</span>').format(value)

# Link id → icon name in _icons.html. An unknown id still renders, with a
# neutral glyph, rather than breaking the sidebar.
LINK_ICONS = {
    "grafana": "grafana",
    "query_history": "history",
    "superset": "superset",
    "gateway_ui": "trino",
}
LINK_DESCRIPTIONS = {
    "grafana": "Metrics & dashboards",
    "query_history": "Completed queries",
    "superset": "SQL Lab",
    "gateway_ui": "Routing & backends",
}

QUERY_STATE_GROUPS = {
    "running": ("RUNNING", "FINISHING"),
    "queued": ("QUEUED", "WAITING_FOR_RESOURCES", "PLANNING", "STARTING", "DISPATCHING"),
}


def link_rows(links_payload: Dict[str, Any]) -> List[Dict[str, str]]:
    rows = []
    for link in links_payload.get("links") or []:
        link_id = str(link.get("id") or "")
        icon = LINK_ICONS.get(link_id)
        if icon is None:
            icon = "trino" if link_id.startswith("trino_ui") else "external"
        description = LINK_DESCRIPTIONS.get(link_id, "")
        if not description and link_id.startswith("trino_ui"):
            description = "Coordinator web UI"
        rows.append(
            {
                "id": link_id,
                "label": link.get("label") or link_id,
                "url": link.get("url") or "",
                "icon": icon,
                "description": description,
            }
        )
    return rows


def _mbean(health_payload: Dict[str, Any], test_id: str) -> Optional[Dict[str, Any]]:
    for test in health_payload.get("tests") or []:
        if test.get("id") == test_id:
            return test
    return None


def cluster_summary(
    name: str,
    expected_workers: int,
    health_envelope: Dict[str, Any],
    queries_envelope: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """One Overview card."""
    health = health_envelope.get("data") or {}
    tests = health.get("tests") or []

    active_workers: Optional[int] = None
    planned_out = 0
    h03 = _mbean(health, "H-03")
    if h03 and isinstance(h03.get("observed_value"), dict):
        observed = h03["observed_value"]
        active_workers = observed.get("active_workers")
        planned_out = observed.get("planned_out") or 0

    failure_rate = None
    h05 = _mbean(health, "H-05")
    if h05 and isinstance(h05.get("observed_value"), (int, float)):
        failure_rate = h05["observed_value"]

    running = queued = 0
    if queries_envelope:
        summary = (queries_envelope.get("data") or {}).get("summary") or {}
        running = summary.get("running") or 0
        queued = summary.get("queued") or 0

    return {
        "name": name,
        "expected_workers": expected_workers,
        "active_workers": active_workers,
        "planned_out": planned_out,
        "running": running,
        "queued": queued,
        "failure_rate": failure_rate,
        "rollup_state": health.get("rollup_state", "UNKNOWN"),
        "stale": bool(health_envelope.get("stale", True)),
        "collected_at": health_envelope.get("collected_at"),
        "tests": [{"id": t.get("id"), "name": t.get("name"), "state": t.get("state")} for t in tests],
    }


def state_counts(tests: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {"good": 0, "concerning": 0, "bad": 0, "unknown": 0}
    for test in tests:
        key = str(test.get("state", "UNKNOWN")).lower()
        if key in counts:
            counts[key] += 1
    return counts


def test_observed_text(test: Dict[str, Any]) -> Markup:
    """The sentence under a health test's name.

    Each test's observation has its own shape, so each gets its own phrasing
    rather than a generic `str(value)` that would print a raw dict at an
    operator mid-incident.
    """
    test_id = test.get("id")
    observed = test.get("observed_value")
    threshold = test.get("threshold")

    if test_id == "H-03" and isinstance(observed, dict):
        parts = [
            _em("{} of {}".format(
                integer(observed.get("active_workers")), integer(observed.get("expected_workers"))
            )) + Markup(" workers active")
        ]
        # The planned/unplanned split is the whole point of H-03 and must
        # survive into the sentence an operator reads.
        if observed.get("planned_out"):
            parts.append(_em(integer(observed["planned_out"])) + Markup(" draining (planned)"))
        if observed.get("unplanned_missing"):
            parts.append(_em(integer(observed["unplanned_missing"])) + Markup(" missing unplanned"))
        return Markup(" · ").join(parts)

    if test_id == "H-04" and isinstance(observed, (int, float)):
        return (_em(percent(observed, 0)) + Markup(" of coordinator heap · threshold ")
                + _mono(percent(threshold, 0)))

    if test_id == "H-05" and isinstance(observed, (int, float)):
        return _em(percent(observed)) + Markup(" of queries failed · last 5m")

    if test_id == "H-06" and isinstance(observed, (int, float)):
        return _em(integer(observed)) + Markup(" internal failures · last 5m")

    if test_id == "H-07" and isinstance(observed, dict):
        delta = observed.get("delta")
        if delta is None:
            return Markup("baseline recorded · total ") + _mono(integer(observed.get("total")))
        return (_em(integer(delta)) + Markup(" new OOM kills since last poll · total ")
                + _mono(integer(observed.get("total"))))

    if isinstance(observed, dict):
        return Markup(" · ").join(
            escape(key) + Markup(" ") + _em(integer(value))
            for key, value in sorted(observed.items())
        )
    if observed is None:
        return Markup("no reading")
    return escape(str(observed))


def health_view(health_envelope: Dict[str, Any]) -> Dict[str, Any]:
    health = dict(health_envelope.get("data") or {})
    tests = []
    for test in health.get("tests") or []:
        row = dict(test)
        row["observed_text"] = test_observed_text(test)
        tests.append(row)
    health["tests"] = tests
    return health


def query_chips(
    summary: Dict[str, Any],
    base_params: Dict[str, str],
    active_state: Optional[str],
    long_running_only: bool,
) -> List[Dict[str, Any]]:
    """Filter chips that are also the summary — the counts are the KPIs."""

    def href(**overrides: Optional[str]) -> str:
        params = {k: v for k, v in base_params.items() if v}
        for key, value in overrides.items():
            if value:
                params[key] = value
            else:
                params.pop(key, None)
        return "/queries" + ("?" + urlencode(params) if params else "")

    return [
        {
            "label": "All",
            "count": summary.get("total", 0),
            "href": href(state=None, long_running=None),
            "active": not active_state and not long_running_only,
            "alert": False,
        },
        {
            "label": "Running",
            "count": summary.get("running", 0),
            "href": href(state="running", long_running=None),
            "active": active_state == "running",
            "alert": False,
        },
        {
            "label": "Queued",
            "count": summary.get("queued", 0),
            "href": href(state="queued", long_running=None),
            "active": active_state == "queued",
            "alert": False,
        },
        {
            "label": "Long-running",
            "count": summary.get("long_running", 0),
            "href": href(state=None, long_running="1"),
            "active": long_running_only,
            "alert": bool(summary.get("long_running")),
        },
    ]


def audit_chips(action_filter: Optional[str], counts: Dict[str, int]) -> List[Dict[str, Any]]:
    def href(action: Optional[str]) -> str:
        return "/audit" + ("?" + urlencode({"action_type": action}) if action else "")

    return [
        {"label": "All", "count": counts.get("all", 0), "href": href(None), "active": not action_filter},
        {
            "label": "Kills",
            "count": counts.get("QUERY_KILL", 0),
            "href": href("QUERY_KILL"),
            "active": action_filter == "QUERY_KILL",
        },
        {
            "label": "Health changes",
            "count": counts.get("HEALTH_TEST_TOGGLE", 0) + counts.get("HEALTH_ROLLUP_TOGGLE", 0),
            "href": href("HEALTH_TEST_TOGGLE"),
            "active": action_filter == "HEALTH_TEST_TOGGLE",
        },
        {
            "label": "Exports",
            "count": counts.get("AUDIT_EXPORT", 0),
            "href": href("AUDIT_EXPORT"),
            "active": action_filter == "AUDIT_EXPORT",
        },
    ]


def expand_state_filter(group: Optional[str]) -> Optional[List[str]]:
    if not group:
        return None
    return list(QUERY_STATE_GROUPS.get(group, ()))or None


BOTTLENECK_TEXT = {
    "queue_full": "Queue full — new queries rejected",
    "concurrency_limit": "At concurrency limit",
    "memory_limit": "At memory limit",
    "cpu_limit": "At CPU limit",
}


def bottleneck_text(reason: Any) -> str:
    """Plain words for a diagnosis code.

    An unknown code renders as itself rather than as an empty cell - a blank
    status next to a highlighted row reads as "no problem", which is the
    opposite of what happened.
    """
    if not reason:
        return ""
    return BOTTLENECK_TEXT.get(str(reason), str(reason))


def flatten_groups(tree: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Depth-first order so the table reads as the tree it represents."""
    rows: List[Dict[str, Any]] = []

    def walk(node: Dict[str, Any]) -> None:
        rows.append(node)
        for child in node.get("children") or []:
            walk(child)

    for root in tree or []:
        walk(root)
    return rows


# Sortable columns for FR-WL-06, and the only values `sort` may take.
WORKLOAD_COLUMNS = (
    {"key": "running", "label": "Running"},
    {"key": "queued", "label": "Queued"},
    {"key": "oldest_queued_ms", "label": "Queue age"},
    {"key": "cpu_ms", "label": "CPU"},
    {"key": "memory_bytes", "label": "Memory"},
    {"key": "input_bytes", "label": "Input"},
)
_SORTABLE = {column["key"] for column in WORKLOAD_COLUMNS}


def column_label(key):
    """Human name for a sort column, for the heading that says what you sorted by.

    Used verbatim rather than lower-cased - "Ranked by cpu" reads as a typo
    where "Ranked by CPU" reads as the column it names.
    """
    for column in WORKLOAD_COLUMNS:
        if column["key"] == key:
            return column["label"]
    return ""


def order_groups(tree, groups, sort=None, descending=True):
    """Rows for the workload table, and whether they are a ranking.

    ⛔ Sorting a tree is a lie. Indentation says "this group is inside that
    one", and once the rows are reordered by CPU that relationship no longer
    holds - the child may sit above a different parent, or above none.

    So ranking is a different view, not a reordered tree: it returns the flat
    group list and tells the caller to stop drawing hierarchy. Default is still
    the tree, because "where does this group sit" is the more common question.

    Returns (rows, ranked).
    """
    if sort not in _SORTABLE:
        return flatten_groups(tree), False

    # Missing is missing, not zero. A group with no reading is unknown, and
    # ranking it as the smallest would assert it is idle - which shows up as
    # "least CPU" putting the groups TMS knows nothing about at the top.
    # Unknowns therefore sit at the end in both directions, rather than being
    # folded into the ordering.
    known = [g for g in groups if g.get(sort) is not None]
    unknown = [g for g in groups if g.get(sort) is None]
    known.sort(key=lambda g: g[sort], reverse=bool(descending))
    return known + unknown, True



# ── work board (FR-BOARD) ─────────────────────────────────────────────


def status_label(status: Optional[str]) -> str:
    from tms.work.items import STATUS_LABELS

    return STATUS_LABELS.get(status or "", status or "—")


def status_choices(current: Optional[str]) -> List[Dict[str, Any]]:
    """Every status, with the current one marked.

    All of them, including the one it is already on: a select that silently
    omits the current value shows the wrong thing selected on first paint.
    """
    from tms.work.items import STATUS_LABELS, STATUS_MEANINGS, STATUS_ORDER

    return [
        {"value": status, "label": STATUS_LABELS[status],
         "meaning": STATUS_MEANINGS[status], "selected": status == current}
        for status in STATUS_ORDER
    ]


def kind_chips(kind_filter: Optional[str], columns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter chips with live counts, taken from the columns already rendered.

    Counted from what is on screen rather than with a second query: two numbers
    fetched separately can disagree, and a chip saying "Requests 3" above two
    request cards is a bug report waiting to happen.
    """
    from tms.work.items import KIND_LABELS

    counts: Dict[str, int] = {}
    total = 0
    for column in columns or []:
        for item in column.get("cards") or []:
            counts[item.get("kind")] = counts.get(item.get("kind"), 0) + 1
            total += 1

    def href(kind: Optional[str]) -> str:
        return "/work" + ("?" + urlencode({"kind": kind}) if kind else "")

    chips = [{"label": "All", "count": total, "href": href(None),
              "active": not kind_filter}]
    for kind, label in KIND_LABELS.items():
        chips.append({"label": label, "count": counts.get(kind, 0),
                      "href": href(kind), "active": kind_filter == kind})
    return chips


def work_item_row(item: Dict[str, Any]) -> Dict[str, Any]:
    from tms.work.items import KIND_LABELS

    row = dict(item or {})
    row["status_label"] = status_label(row.get("status"))
    row["kind_label"] = KIND_LABELS.get(row.get("kind") or "", row.get("kind") or "—")
    # A document-backed item links back to what it points at. The link is a
    # repository path, not a URL: TMS cannot serve the docs and pretending
    # otherwise would give the reader a 404 instead of a place to look.
    row["source_doc"] = (row.get("source_doc") or "").strip() or None
    return row


def work_timeline(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Comments and status changes, interleaved, oldest first.

    One stream rather than two panels: "moved to blocked" and the comment
    saying what blocked it are the same event to the person reading, and
    separating them makes the reader reconstruct the order from timestamps.
    """
    entries: List[Dict[str, Any]] = []
    for comment in item.get("comments") or []:
        entries.append({"kind": "comment", "at": comment.get("created_at"),
                        "actor": comment.get("author"), "body": comment.get("body")})
    for event in item.get("events") or []:
        entries.append({
            "kind": "status", "at": event.get("occurred_at"),
            "actor": event.get("actor"),
            "from_label": status_label(event.get("from_status")),
            "to_label": status_label(event.get("to_status")),
        })
    # Undated entries sort last rather than crashing the comparison: the
    # in-memory repository always sets a time, but a row written by an older
    # migration might not.
    return sorted(entries, key=lambda e: (e["at"] is None, e["at"]))
