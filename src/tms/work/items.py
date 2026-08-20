"""The work board's vocabulary (admin-facing status and requests).

⛔ **The board does not replace the documents.** That is the whole design.

This project has already paid for ignoring it once: `BACKLOG.md` and
`REQUIREMENTS.md` appendix B disagreed about where three features sat, for
weeks, because both claimed to be the answer. A screen that restated what the
documents say would become a fourth claimant.

So the split is deliberate and narrow:

    the DOCUMENT owns the reasoning - why a decision went the way it did, what
                                      a requirement means, what blocks it.
                                      Reviewed in Git, changes by pull request.
    the BOARD owns the status       - where the item sits today, and the
                                      conversation about it. Changes often, by
                                      people who do not send pull requests.

Every item that has a write-up carries `source_doc`, and the screen says so.
An item with no document is a request nobody has written up yet - which is
precisely the thing that had nowhere to live before.

Python 3.9 compatible.
"""

from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------- statuses

NEEDS_DECISION = "needs_decision"
BLOCKED = "blocked"
PLANNED = "planned"
IN_PROGRESS = "in_progress"
DONE = "done"
DROPPED = "dropped"

#: Board order, and it is not alphabetical. What needs a person comes first,
#: because an item waiting on a decision is the only kind nothing else can
#: unstick - everything below it is already moving or already waiting on
#: something named.
STATUS_ORDER = (NEEDS_DECISION, BLOCKED, IN_PROGRESS, PLANNED, DONE, DROPPED)

STATUS_LABELS = {
    NEEDS_DECISION: "Needs a decision",
    BLOCKED: "Blocked",
    IN_PROGRESS: "In progress",
    PLANNED: "Planned",
    DONE: "Done",
    DROPPED: "Dropped",
}

#: What each status means in one line. Shown on the board, because a status
#: vocabulary nobody agrees on is worse than no vocabulary.
STATUS_MEANINGS = {
    NEEDS_DECISION: "Waiting on a person. Nothing else will move this.",
    BLOCKED: "Waiting on something named — see what it is blocked by.",
    IN_PROGRESS: "Being built now.",
    PLANNED: "Agreed and unblocked, not started.",
    DONE: "Built and in the repository.",
    DROPPED: "Decided against. Kept so the reasoning stays findable.",
}

OPEN_STATUSES = (NEEDS_DECISION, BLOCKED, IN_PROGRESS, PLANNED)

# ------------------------------------------------------------------- kinds

DECISION = "decision"
REQUIREMENT = "requirement"
REQUEST = "request"
TASK = "task"

KIND_LABELS = {
    DECISION: "Decision",
    REQUIREMENT: "Requirement",
    REQUEST: "Request",
    TASK: "Task",
}

#: Requests are what administrators raise here. Everything else arrives from a
#: document, so the form offers only this one - an admin filing something as a
#: "decision" would be creating a decision record outside DECISIONS.md, which
#: is the divergence this design exists to prevent.
REQUESTABLE_KINDS = (REQUEST,)


class WorkItemError(Exception):
    """The item as described cannot be stored."""


def validate(kind: str, status: str, title: str, key: Optional[str] = None) -> None:
    if kind not in KIND_LABELS:
        raise WorkItemError("{!r} is not a kind of work item.".format(kind))
    if status not in STATUS_LABELS:
        raise WorkItemError("{!r} is not a status.".format(status))
    if not (title or "").strip():
        raise WorkItemError("An item needs a title.")
    if key is not None and not (key or "").strip():
        raise WorkItemError("An item needs a key.")


def next_request_key(existing_keys) -> str:
    """REQ-1, REQ-2, ... - the only keys the board mints itself.

    Document-backed items keep the identifier their document already gave them
    (`D-012`, `FR-BM-01`), because that identifier is what people paste into
    chat and what the document is titled with. Renumbering those here would
    break the link in both directions.
    """
    highest = 0
    for key in existing_keys or []:
        text = str(key or "")
        if text.upper().startswith("REQ-"):
            try:
                highest = max(highest, int(text.split("-", 1)[1]))
            except (IndexError, ValueError):
                continue
    return "REQ-{}".format(highest + 1)


def group_by_status(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The board, in STATUS_ORDER, with empty columns kept.

    An empty "Needs a decision" column is information - it says nothing is
    waiting on you. Hiding it would make the reader work out the difference
    between "nothing waiting" and "that column does not exist here".
    """
    buckets = {status: [] for status in STATUS_ORDER}
    for item in items or []:
        buckets.setdefault(item.get("status"), []).append(item)
    return [
        {
            "status": status,
            "label": STATUS_LABELS[status],
            "meaning": STATUS_MEANINGS[status],
            # "cards", not "items": a Jinja template asking for `column.items`
            # gets dict.items, the method, and renders a length of a bound
            # method rather than a count. Naming it away from a dict attribute
            # is cheaper than remembering bracket syntax in every template.
            "cards": buckets.get(status) or [],
        }
        for status in STATUS_ORDER
    ]


def summarise(items: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {status: 0 for status in STATUS_ORDER}
    for item in items or []:
        if item.get("status") in counts:
            counts[item["status"]] += 1
    counts["open"] = sum(counts[s] for s in OPEN_STATUSES)
    return counts
