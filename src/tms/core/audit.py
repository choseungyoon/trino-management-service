"""Audit enforcement for write actions (FR-AUDIT-ACTION).

The rules from docs/AUDIT_MODEL.md, and why each is enforced here rather than in
individual handlers:

* AU1 - if the action cannot be audited, it does not happen. Writability is
  checked *before* the action runs, so a dead audit store yields 503 instead of
  an unrecorded kill.
* AU2 - a blank reason is a 400. Whitespace counts as blank.
* AU3 - append-only. There is no update or delete path in this module, and the
  database grants withhold the privilege as a second line of defence.
* AU4 - the actor is the human who asked, never the tms-svc service account.
* AU5 - failures are recorded too, including authorisation refusals. "Why did
  nothing happen?" is an audit question.

Enforcement lives in a context manager rather than in each route because a rule
that every handler must remember is a rule that will eventually be forgotten.

Python 3.9 compatible.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

SUCCESS = "SUCCESS"
FAILURE = "FAILURE"

# The complete set of R1 write actions. Adding one is a requirements change,
# not an implementation detail, so the database enforces the same list.
ACTION_QUERY_KILL = "QUERY_KILL"
ACTION_HEALTH_TEST_TOGGLE = "HEALTH_TEST_TOGGLE"
ACTION_HEALTH_ROLLUP_TOGGLE = "HEALTH_ROLLUP_TOGGLE"
ACTION_HEALTH_THRESHOLD_CHANGE = "HEALTH_THRESHOLD_CHANGE"
ACTION_AUDIT_EXPORT = "AUDIT_EXPORT"
# R3 FR-CO-02. Every step of a restart sequence is audited under this
# type; the step itself is in `details`.
ACTION_CLUSTER_RESTART = "CLUSTER_RESTART"
# FR-FL-03. Taking a worker out of the cluster is a write like any other:
# reason, audit, admin only.
ACTION_NODE_SHUTDOWN = "NODE_SHUTDOWN"

ALLOWED_ACTION_TYPES = frozenset(
    [
        ACTION_QUERY_KILL,
        ACTION_HEALTH_TEST_TOGGLE,
        ACTION_HEALTH_ROLLUP_TOGGLE,
        ACTION_HEALTH_THRESHOLD_CHANGE,
        ACTION_AUDIT_EXPORT,
        ACTION_CLUSTER_RESTART,
        ACTION_NODE_SHUTDOWN,
    ]
)

TARGET_QUERY = "query"
TARGET_CLUSTER = "cluster"
TARGET_HEALTH_TEST = "health_test"
TARGET_NODE = "node"

# The reason is forwarded to Trino and shown to the user whose query was killed,
# so it is capped and flattened to a single line.
MAX_REASON_LENGTH = 512


class AuditError(Exception):
    """Base class for audit refusals."""


class ReasonRequired(AuditError):
    """AU2. Maps to HTTP 400 / REASON_REQUIRED."""


class AuditUnavailable(AuditError):
    """AU1. Maps to HTTP 503 / AUDIT_UNAVAILABLE.

    Raised *before* the action runs. There is deliberately no bypass: an action
    that cannot be recorded must not be performed.
    """


class InvalidActionType(AuditError):
    """A write action outside the R1 catalogue."""


@dataclass
class AuditRecord:
    actor: str
    action_type: str
    target_kind: str
    target_id: str
    reason: str
    outcome: str
    request_id: str
    actor_roles: List[str] = field(default_factory=list)
    actor_ip: Optional[str] = None
    target_cluster: Optional[str] = None
    error_message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    occurred_at: Optional[datetime] = None


def normalise_reason(reason: Optional[str]) -> str:
    """Validate and flatten a reason. Raises ReasonRequired when blank.

    Newlines are collapsed because this text is surfaced to end users as an
    error message, and the length is capped for the same reason.
    """
    if reason is None:
        raise ReasonRequired("reason is required for write actions")
    flattened = " ".join(str(reason).split())
    if not flattened:
        raise ReasonRequired("reason is required for write actions")
    if len(flattened) > MAX_REASON_LENGTH:
        flattened = flattened[: MAX_REASON_LENGTH - 3] + "..."
    return flattened


class AuditRepository:
    """Storage interface. Append-only by construction: no update, no delete."""

    def write(self, record: AuditRecord) -> None:
        raise NotImplementedError

    def is_writable(self) -> bool:
        """Checked before the action runs so AU1 can be honoured."""
        raise NotImplementedError

    def search(
        self,
        actor: Optional[str] = None,
        action_type: Optional[str] = None,
        target_kind: Optional[str] = None,
        target_id: Optional[str] = None,
        outcome: Optional[str] = None,
        occurred_from: Optional[Any] = None,
        occurred_to: Optional[Any] = None,
        limit: int = 100,
        **_ignored: Any
    ) -> List[AuditRecord]:
        """Newest first. Implementations must keep this signature: the in-memory
        one silently returning nothing because it treated `limit` as a column
        filter is a mistake worth only making once."""
        raise NotImplementedError


class InMemoryAuditRepository(AuditRepository):
    """Tests and dry runs only."""

    def __init__(self, writable: bool = True) -> None:
        self.records: List[AuditRecord] = []
        self.writable = writable

    def write(self, record: AuditRecord) -> None:
        if not self.writable:
            raise AuditUnavailable("audit store is unavailable")
        self.records.append(record)

    def is_writable(self) -> bool:
        return self.writable

    def search(
        self,
        actor: Optional[str] = None,
        action_type: Optional[str] = None,
        target_kind: Optional[str] = None,
        target_id: Optional[str] = None,
        outcome: Optional[str] = None,
        occurred_from: Optional[Any] = None,
        occurred_to: Optional[Any] = None,
        limit: int = 100,
        **_ignored: Any
    ) -> List[AuditRecord]:
        results = list(self.records)
        for attribute, value in (
            ("actor", actor),
            ("action_type", action_type),
            ("target_kind", target_kind),
            ("target_id", target_id),
            ("outcome", outcome),
        ):
            if value is not None:
                results = [r for r in results if getattr(r, attribute, None) == value]
        if occurred_from is not None:
            results = [r for r in results if r.occurred_at and r.occurred_at >= occurred_from]
        if occurred_to is not None:
            results = [r for r in results if r.occurred_at and r.occurred_at <= occurred_to]
        results.sort(key=lambda r: r.occurred_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return results[: max(1, int(limit))]


class _AuditedAction:
    """Context manager returned by AuditGuard.action().

    Writes exactly one record on exit, carrying the real outcome. A pending
    intermediate state is deliberately avoided: in an append-only table there
    would be no way to clean up a record left behind by a crashed process.
    """

    def __init__(self, guard: "AuditGuard", record: AuditRecord) -> None:
        self._guard = guard
        self.record = record
        self.details: Dict[str, Any] = {}

    @property
    def request_id(self) -> str:
        return self.record.request_id

    def __enter__(self) -> "_AuditedAction":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self.record.occurred_at = datetime.now(timezone.utc)
        if self.details:
            self.record.details = self.details
        if exc_type is None:
            self.record.outcome = SUCCESS
        else:
            self.record.outcome = FAILURE
            self.record.error_message = "{}: {}".format(exc_type.__name__, exc_value)
        try:
            self._guard.repository.write(self.record)
        except Exception:  # noqa: BLE001
            # The action already happened; losing the record is bad but raising
            # here would replace the real error with a storage error.
            log.exception(
                "failed to write audit record for %s on %s - ACTION ALREADY PERFORMED",
                self.record.action_type,
                self.record.target_id,
            )
        return False  # never suppress


class AuditGuard:
    """Entry point for every write action.

    Usage:

        with guard.action(
            actor="syhcho", roles=["operator"], action_type=ACTION_QUERY_KILL,
            target_kind=TARGET_QUERY, target_id=query_id, reason=reason,
            target_cluster="prod-a",
        ) as audited:
            client.kill_query(query_id, build_kill_message(...))

    Anything that raises inside the block is recorded as FAILURE and re-raised.
    """

    def __init__(self, repository: AuditRepository) -> None:
        self.repository = repository

    def action(
        self,
        actor: str,
        roles: List[str],
        action_type: str,
        target_kind: str,
        target_id: str,
        reason: Optional[str],
        target_cluster: Optional[str] = None,
        actor_ip: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> _AuditedAction:
        if action_type not in ALLOWED_ACTION_TYPES:
            raise InvalidActionType(
                "{} is not an R1 audit action; adding one requires a "
                "requirements change".format(action_type)
            )

        # Order matters: reject a blank reason before touching storage so a
        # malformed request is a 400 rather than a 503.
        clean_reason = normalise_reason(reason)

        if not self.repository.is_writable():
            raise AuditUnavailable(
                "audit store is unavailable; refusing to perform {} on {}".format(
                    action_type, target_id
                )
            )

        record = AuditRecord(
            actor=actor,
            actor_roles=list(roles or []),
            actor_ip=actor_ip,
            action_type=action_type,
            target_kind=target_kind,
            target_id=target_id,
            target_cluster=target_cluster,
            reason=clean_reason,
            outcome=FAILURE,  # replaced on exit; never left as a guess
            request_id=request_id or str(uuid.uuid4()),
        )
        return _AuditedAction(self, record)

    def record_refusal(
        self,
        actor: str,
        roles: List[str],
        action_type: str,
        target_kind: str,
        target_id: str,
        reason: Optional[str],
        error_message: str,
        target_cluster: Optional[str] = None,
        actor_ip: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Optional[str]:
        """Record an action that was refused before it began - a 403, typically.

        AU5: "why did nothing happen?" is an audit question. A refusal with a
        blank reason still gets recorded, with a placeholder, because the
        refusal itself is the fact worth keeping.
        """
        try:
            clean_reason = normalise_reason(reason)
        except ReasonRequired:
            clean_reason = "(reason not supplied)"

        record = AuditRecord(
            actor=actor,
            actor_roles=list(roles or []),
            actor_ip=actor_ip,
            action_type=action_type,
            target_kind=target_kind,
            target_id=target_id,
            target_cluster=target_cluster,
            reason=clean_reason,
            outcome=FAILURE,
            error_message=error_message,
            request_id=request_id or str(uuid.uuid4()),
            occurred_at=datetime.now(timezone.utc),
        )
        try:
            self.repository.write(record)
        except Exception:  # noqa: BLE001 - a lost refusal must not mask the 403
            log.exception("failed to record refusal for %s", action_type)
            return None
        return record.request_id
