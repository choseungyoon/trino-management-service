"""PostgreSQL-backed snapshot repository.

psycopg is imported lazily so the polling logic stays importable and testable in
environments without a database driver.

The advisory lock here is the mechanism behind the "single instance" comment in
tms-collector.service. A comment asks people not to run two collectors; this
makes the second one exit. Two collectors would double the load on every
coordinator and quietly break NFR-PERF-03 (ARCHITECTURE.md principle A3).

Python 3.9 compatible.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from tms.collector.snapshot import Snapshot, SnapshotRepository

log = logging.getLogger(__name__)

# Arbitrary but fixed: the key identifies "the TMS collector" cluster-wide.
COLLECTOR_ADVISORY_LOCK_KEY = 0x746D7301


class PostgresSnapshotRepository(SnapshotRepository):
    def __init__(self, dsn: str) -> None:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "psycopg is required for PostgresSnapshotRepository; install the "
                "tms package dependencies"
            ) from exc
        self._psycopg = psycopg
        self._dsn = dsn
        self._connection = psycopg.connect(dsn, autocommit=True)

    # ------------------------------------------------------- single instance

    def acquire_singleton_lock(self) -> bool:
        """Session-scoped advisory lock. False means another collector holds it.

        Released automatically when the connection drops, so a crashed collector
        does not lock out its replacement.
        """
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_lock(%s)", (COLLECTOR_ADVISORY_LOCK_KEY,)
            )
            row = cursor.fetchone()
        return bool(row and row[0])

    # ------------------------------------------------------------ snapshots

    def save(self, snapshot: Snapshot) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO collector_snapshot
                    (cluster, kind, collected_at, payload, collection_error)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (cluster, kind) DO UPDATE SET
                    collected_at = EXCLUDED.collected_at,
                    payload = EXCLUDED.payload,
                    collection_error = EXCLUDED.collection_error
                """,
                (
                    snapshot.cluster,
                    snapshot.kind,
                    snapshot.collected_at,
                    json.dumps(self._with_advice(snapshot), default=str),
                    snapshot.collection_error,
                ),
            )

    @staticmethod
    def _with_advice(snapshot: Snapshot) -> Dict[str, Any]:
        payload = dict(snapshot.payload)
        if snapshot.advice:
            # Carried in the payload so the API can render the remedy without a
            # second lookup. A failure with no remedy must never reach the UI.
            payload["_advice"] = snapshot.advice
        return payload

    def load(self, cluster: str, kind: str) -> Optional[Snapshot]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT collected_at, payload, collection_error
                FROM collector_snapshot
                WHERE cluster = %s AND kind = %s
                """,
                (cluster, kind),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        collected_at, payload, collection_error = row
        if isinstance(payload, str):
            payload = json.loads(payload)
        return Snapshot(
            cluster=cluster,
            kind=kind,
            collected_at=collected_at,
            payload=payload or {},
            collection_error=collection_error,
            advice=(payload or {}).get("_advice"),
        )

    def record_health_events(self, events: List[Dict[str, Any]]) -> None:
        if not events:
            return
        with self._connection.cursor() as cursor:
            for event in events:
                cursor.execute(
                    """
                    INSERT INTO health_event
                        (cluster, test_id, from_state, to_state,
                         observed_value, threshold, advice)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        event["cluster"],
                        event["test_id"],
                        event["from_state"],
                        event["to_state"],
                        str(event.get("observed_value")),
                        str(event.get("threshold")),
                        event.get("advice"),
                    ),
                )

    # ------------------------------------------------------ health overrides

    def save_health_override(
        self,
        cluster: str,
        test_id: str,
        enabled: Optional[bool],
        thresholds: Optional[Dict[str, Any]],
        updated_by: str,
    ) -> None:
        """Upsert an override. COALESCE keeps the untouched half intact so that
        changing a threshold does not silently re-enable a disabled test."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO health_test_override
                    (cluster, test_id, enabled, thresholds, updated_at, updated_by)
                VALUES (%s, %s, COALESCE(%s, TRUE), %s, now(), %s)
                ON CONFLICT (cluster, test_id) DO UPDATE SET
                    enabled = COALESCE(%s, health_test_override.enabled),
                    thresholds = COALESCE(%s, health_test_override.thresholds),
                    updated_at = now(),
                    updated_by = EXCLUDED.updated_by
                """,
                (
                    cluster,
                    test_id,
                    enabled,
                    json.dumps(thresholds) if thresholds else None,
                    updated_by,
                    enabled,
                    json.dumps(thresholds) if thresholds else None,
                ),
            )

    def load_health_overrides(self, cluster: str) -> Dict[str, Dict[str, Any]]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT test_id, enabled, thresholds FROM health_test_override WHERE cluster = %s",
                (cluster,),
            )
            rows = cursor.fetchall()
        overrides: Dict[str, Dict[str, Any]] = {}
        for test_id, enabled, thresholds in rows:
            if isinstance(thresholds, str):
                thresholds = json.loads(thresholds)
            overrides[test_id] = {"enabled": enabled, "thresholds": thresholds or {}}
        return overrides

    def close(self) -> None:
        try:
            self._connection.close()
        except Exception:  # noqa: BLE001 - shutdown must not raise
            log.exception("failed to close the database connection")
