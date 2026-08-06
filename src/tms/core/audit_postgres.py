"""PostgreSQL-backed audit repository.

INSERT and SELECT only. There is no UPDATE or DELETE statement anywhere in this
file, and `migrations/002_grants.sql` withholds the privilege from the
application role as a second line of defence (AUDIT_MODEL.md AU3).

If a future change appears to need an UPDATE here, it is a design error:
corrections to an audit log are new rows, not edits.

psycopg is imported lazily so the enforcement logic stays testable without a
database driver installed.

Python 3.9 compatible.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from tms.core.audit import AuditRecord, AuditRepository, AuditUnavailable

log = logging.getLogger(__name__)

_INSERT = """
INSERT INTO audit_action
    (occurred_at, actor, actor_roles, actor_ip, action_type, target_kind,
     target_id, target_cluster, reason, outcome, error_message, request_id, details)
VALUES
    (COALESCE(%s, now()), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

_SELECT_COLUMNS = """
    occurred_at, actor, actor_roles, actor_ip, action_type, target_kind,
    target_id, target_cluster, reason, outcome, error_message, request_id, details, id
"""


class PostgresAuditRepository(AuditRepository):
    def __init__(self, dsn: str) -> None:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "psycopg is required for PostgresAuditRepository; install the "
                "tms package dependencies"
            ) from exc
        self._psycopg = psycopg
        self._dsn = dsn
        self._connection = psycopg.connect(dsn, autocommit=True)

    def is_writable(self) -> bool:
        """Cheap liveness probe run before every write action (AU1).

        Deliberately not a cached flag: the point is to know the state *now*,
        just before performing something irreversible.
        """
        try:
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            return True
        except Exception:  # noqa: BLE001
            log.warning("audit store is not writable", exc_info=True)
            try:
                self._connection = self._psycopg.connect(self._dsn, autocommit=True)
                return True
            except Exception:  # noqa: BLE001
                return False

    def write(self, record: AuditRecord) -> None:
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    _INSERT,
                    (
                        record.occurred_at,
                        record.actor,
                        list(record.actor_roles or []),
                        record.actor_ip,
                        record.action_type,
                        record.target_kind,
                        record.target_id,
                        record.target_cluster,
                        record.reason,
                        record.outcome,
                        record.error_message,
                        record.request_id,
                        json.dumps(record.details) if record.details else None,
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            raise AuditUnavailable("failed to write audit record: {}".format(exc)) from exc

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
        cursor_occurred_at: Optional[Any] = None,
        cursor_id: Optional[int] = None,
    ) -> List[AuditRecord]:
        """Keyset pagination on (occurred_at DESC, id DESC).

        OFFSET degrades badly once the log grows, and this table only ever grows.
        """
        clauses = []
        params: List[Any] = []
        for column, value in (
            ("actor", actor),
            ("action_type", action_type),
            ("target_kind", target_kind),
            ("target_id", target_id),
            ("outcome", outcome),
        ):
            if value is not None:
                clauses.append("{} = %s".format(column))
                params.append(value)
        if occurred_from is not None:
            clauses.append("occurred_at >= %s")
            params.append(occurred_from)
        if occurred_to is not None:
            clauses.append("occurred_at <= %s")
            params.append(occurred_to)
        if cursor_occurred_at is not None and cursor_id is not None:
            clauses.append("(occurred_at, id) < (%s, %s)")
            params.extend([cursor_occurred_at, cursor_id])

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = "SELECT {cols} FROM audit_action {where} ORDER BY occurred_at DESC, id DESC LIMIT %s".format(
            cols=_SELECT_COLUMNS, where=where
        )
        params.append(max(1, min(int(limit), 1000)))

        with self._connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        return [self._to_record(row) for row in rows]

    @staticmethod
    def _to_record(row: Any) -> AuditRecord:
        (
            occurred_at,
            actor,
            actor_roles,
            actor_ip,
            action_type,
            target_kind,
            target_id,
            target_cluster,
            reason,
            outcome,
            error_message,
            request_id,
            details,
            _row_id,
        ) = row
        if isinstance(details, str):
            details = json.loads(details)
        return AuditRecord(
            actor=actor,
            actor_roles=list(actor_roles or []),
            actor_ip=str(actor_ip) if actor_ip is not None else None,
            action_type=action_type,
            target_kind=target_kind,
            target_id=target_id,
            target_cluster=target_cluster,
            reason=reason,
            outcome=outcome,
            error_message=error_message,
            request_id=str(request_id),
            details=details,
            occurred_at=occurred_at,
        )

    def close(self) -> None:
        try:
            self._connection.close()
        except Exception:  # noqa: BLE001
            log.exception("failed to close the audit database connection")
