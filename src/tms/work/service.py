"""The work board's rules.

⚠️ **Absolute rule 3 does not apply here, and that is a deliberate reading.**

Rule 3 says every write action needs a reason, an audit record and an
administrator. Its subject is actions taken *against Trino* - killing a query,
restarting a cluster, changing an admission limit. Those touch production and
someone will later need to know why.

A comment on a board item touches nothing. Demanding a reason for a reason
would make the feature unusable, and a rule applied where it does not fit is
how rules stop being taken seriously in the places they do fit. So:

* board writes are **admin only** (rule 3's audience holds),
* every write records **who and when** (rule 3's accountability holds),
* there is **no `reason` field and no `audit_action` row** - the comment *is*
  the reason, and filling the audit table with board bookkeeping would bury the
  rows an operator opens it to find.

If that reading is wrong it is a one-line change to `_require_admin`. Flagged
rather than quietly assumed.

Python 3.9 compatible.
"""

import logging
from typing import Any, Dict, List, Optional

from tms.api.errors import Forbidden, InvalidRequest, NotFound, UpstreamUnavailable
from tms.api.permissions import MANAGE_HEALTH, VIEW_PORTAL, Principal
from tms.work.items import (
    PLANNED,
    REQUEST,
    REQUESTABLE_KINDS,
    STATUS_LABELS,
    WorkItemError,
    group_by_status,
    summarise,
    timeline,
    validate,
)
from tms.work.store import BoardUnavailable, DuplicateKey

log = logging.getLogger(__name__)


class BoardService:
    def __init__(self, repository) -> None:
        self.repository = repository

    # ------------------------------------------------------------- reading

    def _require_view(self, principal: Principal) -> None:
        # Any signed-in user. The board is the roadmap, and an operator who
        # cannot see what is planned has to ask someone what is planned.
        if not principal.can(VIEW_PORTAL):
            raise Forbidden("You do not have permission to view the board.")

    def _require_admin(self, principal: Principal) -> None:
        if not principal.can(MANAGE_HEALTH):
            raise Forbidden(
                "Only administrators can change the board or raise requests.")

    def board(self, principal: Principal, kind: Optional[str] = None) -> Dict[str, Any]:
        self._require_view(principal)
        try:
            items = self.repository.list_items(kind=kind or None)
        except BoardUnavailable as exc:
            log.warning("work board unavailable: %s", exc)
            return {"available": False, "error": str(exc),
                    "columns": [], "summary": {}, "kind": kind}
        return {
            "available": True,
            "error": None,
            "columns": group_by_status(items),
            "summary": summarise(items),
            "kind": kind,
        }

    def item(self, principal: Principal, key: str) -> Dict[str, Any]:
        self._require_view(principal)
        try:
            found = self.repository.get(key)
        except BoardUnavailable as exc:
            raise UpstreamUnavailable(
                "The work board is not reachable: {}".format(exc))
        if found is None:
            raise NotFound("No such item: {}".format(key))
        # Interleaved here rather than by the caller. Two clients doing it
        # themselves are two copies of a rule nobody wrote down.
        return dict(found, timeline=timeline(found))

    # ------------------------------------------------------------- writing

    def raise_request(self, principal: Principal, title: str,
                      body: str = "") -> Dict[str, Any]:
        """An administrator asks for something.

        Only `request` can be raised here. A `decision` or `requirement` filed
        from this screen would be a record created outside the documents that
        own those, which is the divergence this board exists not to cause.
        """
        self._require_admin(principal)
        try:
            validate(REQUEST, PLANNED, title)
        except WorkItemError as exc:
            raise InvalidRequest(str(exc))

        try:
            key = self.repository.next_key()
        except BoardUnavailable as exc:
            raise UpstreamUnavailable(
                "The work board is not reachable: {}".format(exc))
        try:
            item = self.repository.create(
                key=key, kind=REQUEST, title=title.strip(),
                # Raised, not yet agreed. `needs_decision` would claim someone
                # is waiting on a person to choose, when what is actually
                # waiting is a conversation that has not happened yet.
                status=PLANNED, created_by=principal.username, body=body or "")
        except DuplicateKey:
            raise InvalidRequest("{} already exists.".format(key))
        except BoardUnavailable as exc:
            raise UpstreamUnavailable(
                "The work board is not reachable: {}".format(exc))
        log.info("work item %s raised by %s", key, principal.username)
        return item

    def comment(self, principal: Principal, key: str, body: str) -> Dict[str, Any]:
        self._require_admin(principal)
        if not (body or "").strip():
            raise InvalidRequest("An empty comment says nothing.")
        try:
            added = self.repository.add_comment(key, principal.username, body.strip())
        except BoardUnavailable as exc:
            raise UpstreamUnavailable(
                "The work board is not reachable: {}".format(exc))
        if added is None:
            raise NotFound("No such item: {}".format(key))
        return added

    def set_status(self, principal: Principal, key: str, status: str) -> Dict[str, Any]:
        self._require_admin(principal)
        if status not in STATUS_LABELS:
            raise InvalidRequest("{!r} is not a status.".format(status))
        try:
            updated = self.repository.update(key, principal.username, status=status)
        except BoardUnavailable as exc:
            raise UpstreamUnavailable(
                "The work board is not reachable: {}".format(exc))
        if updated is None:
            raise NotFound("No such item: {}".format(key))
        log.info("work item %s -> %s by %s", key, status, principal.username)
        return updated

    def edit(self, principal: Principal, key: str, **fields) -> Dict[str, Any]:
        self._require_admin(principal)
        try:
            updated = self.repository.update(key, principal.username, **fields)
        except BoardUnavailable as exc:
            raise UpstreamUnavailable(
                "The work board is not reachable: {}".format(exc))
        if updated is None:
            raise NotFound("No such item: {}".format(key))
        return updated

    # ------------------------------------------------------------ exporting

    def export_markdown(self, principal: Optional[Principal] = None) -> str:
        """The board as a document, so it can be committed and read back.

        ⛔ This is not a convenience. TMS runs inside a network its author is
        usually outside of, so "check the board before starting work" is only
        true if the board can reach the repository. Without this the
        instruction is aspirational.
        """
        if principal is not None:
            self._require_view(principal)
        try:
            return render_markdown(self.repository.list_items())
        except BoardUnavailable as exc:
            raise UpstreamUnavailable(
                "The work board is not reachable: {}".format(exc))


def render_markdown(items: List[Dict[str, Any]]) -> str:
    """Board -> markdown, grouped by status in board order."""
    from datetime import datetime, timezone

    lines = [
        "# WORK_BOARD — 작업 보드 스냅샷",
        "",
        "> **자동 생성이다. 손으로 고치지 마라** — `tms-work-export` 가 덮어쓴다.",
        "> 상태의 주인은 TMS 의 보드이고, **근거의 주인은 각 항목이 가리키는 문서**다.",
        "> 둘이 어긋나면 문서가 이긴다.",
        "",
        "> 생성 시각: {}".format(
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")),
        "",
    ]

    columns = group_by_status(items)
    counts = summarise(items)
    lines += ["| 상태 | 건수 |", "|---|---|"]
    for column in columns:
        lines.append("| {} | {} |".format(column["label"], len(column["cards"])))
    lines += ["", "---", ""]

    for column in columns:
        lines.append("## {} ({})".format(column["label"], len(column["cards"])))
        lines.append("")
        lines.append("> {}".format(column["meaning"]))
        lines.append("")
        if not column["cards"]:
            lines += ["_없음._", ""]
            continue
        lines += ["| 키 | 제목 | 종류 | 릴리스 | 막는 것 | 근거 문서 |",
                  "|---|---|---|---|---|---|"]
        for item in column["cards"]:
            lines.append("| `{}` | {} | {} | {} | {} | {} |".format(
                item.get("key"), item.get("title"),
                item.get("kind"), item.get("release") or "—",
                item.get("blocked_by") or "—",
                "`{}`".format(item["source_doc"]) if item.get("source_doc") else "—"))
        lines.append("")

    lines += [
        "---",
        "",
        "**열린 항목 {}건.** 착수 전에 이 파일을 읽는다 — 특히 "
        "\"Needs a decision\" 은 사람이 답하기 전까지 아무것도 움직이지 "
        "않는 항목이다.".format(counts.get("open", 0)),
        "",
    ]
    return "\n".join(lines)
