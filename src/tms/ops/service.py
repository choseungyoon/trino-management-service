"""Driving the restart sequence (FR-CO-02).

`sequence.py` decides what may happen; this makes it happen — deactivating in
the Gateway, reading the running query count, running the restart, reactivating.

Two rules shape everything here:

* **Nothing advances without an observation.** Every step that depends on the
  cluster's state re-reads it first. A cached "0 running queries" from a minute
  ago is not evidence that a restart is safe now.
* **Traffic is restored on every exit path.** Abort restores it, a failed
  restart leaves the sequence in a state whose only forward move is abort, and
  abort itself is not finished until the Gateway confirms the cluster is
  active. A sequence that ends with traffic still blocked is an outage that
  looks like health.

Every transition is audited with the operator's reason, and the write actions
follow the same rule as the rest of TMS: no reason, no action.

Python 3.9 compatible.
"""

import logging
from typing import Any, Dict, List, Optional

from tms.api.errors import Forbidden, InvalidRequest, NotFound, UpstreamUnavailable
from tms.api.permissions import MANAGE_HEALTH, Principal
from tms.clients.errors import TrinoClientError
from tms.collector.snapshot import KIND_HEALTH, KIND_QUERIES
from tms.core.audit import ACTION_CLUSTER_RESTART, TARGET_CLUSTER, AuditGuard
from tms.ops.executor import FAILED, PENDING_OPERATOR, SUCCEEDED
from tms.ops.repository import ActiveSequenceExists, SequenceUnavailable
from tms.ops.sequence import (
    DRAINED,
    DRAINING,
    RESTARTING,
    VERIFYING,
    RestartSequence,
    SequenceError,
    StepBlocked,
)

log = logging.getLogger(__name__)

class RestartService:
    def __init__(self, config, repository, snapshots, gateway_client,
                 audit_guard: AuditGuard, executor) -> None:
        self.config = config
        self.repository = repository
        self.snapshots = snapshots
        self.gateway = gateway_client
        self.audit = audit_guard
        self.executor = executor

    # ------------------------------------------------------------- helpers

    def _require_admin(self, principal: Principal) -> None:
        # Restarting a cluster is the most destructive thing TMS can do.
        if not principal.can(MANAGE_HEALTH):
            raise Forbidden("You do not have permission to restart a cluster.")

    def _cluster_or_404(self, cluster: str):
        try:
            return self.config.cluster(cluster)
        except KeyError:
            raise NotFound("Unknown cluster: {}".format(cluster))

    def _audited(self, principal: Principal, cluster: str, reason: str):
        """Context manager that records the step and its outcome.

        The guard refuses a blank reason and refuses to run at all when the
        audit store is down (AU1), so an unrecorded restart step is not
        reachable from here.
        """
        return self.audit.action(
            actor=principal.username, roles=principal.roles,
            action_type=ACTION_CLUSTER_RESTART, target_kind=TARGET_CLUSTER,
            target_id=cluster, target_cluster=cluster, reason=reason,
            actor_ip=principal.ip,
        )

    def _observe(self, stored) -> None:
        """Re-read the cluster before any decision that depends on it."""
        cluster = stored.sequence.cluster
        running = None
        snapshot = self.snapshots.load(cluster, KIND_QUERIES)
        if snapshot is not None and snapshot.trustworthy:
            summary = (snapshot.payload or {}).get("summary") or {}
            running = int(summary.get("running") or 0) + int(summary.get("queued") or 0)

        health = None
        health_snapshot = self.snapshots.load(cluster, KIND_HEALTH)
        if health_snapshot is not None:
            health = (health_snapshot.payload or {}).get("rollup_state")

        if running is not None:
            stored.sequence.observe(running_queries=running, health_state=health)
        elif health is not None:
            stored.sequence.health_state = health

    def _set_gateway_active(self, cluster: str, active: bool) -> None:
        if self.gateway is None:
            raise UpstreamUnavailable(
                "The Gateway integration is off, so TMS cannot stop traffic to "
                "this cluster. A restart without stopping intake would kill "
                "running queries.")
        backend = self._backend_name(cluster)
        self.gateway.set_active(backend, active)

    def _backend_name(self, cluster: str) -> str:
        """Map a TMS cluster to its Gateway backend.

        Names drift between the two sides, so the Gateway snapshot's URL-based
        join is the source of truth. Guessing that they are equal is how a
        deactivate silently targets the wrong cluster.
        """
        from tms.collector.snapshot import GATEWAY_SCOPE, KIND_GATEWAY

        snapshot = self.snapshots.load(GATEWAY_SCOPE, KIND_GATEWAY)
        for backend in ((snapshot.payload if snapshot else {}) or {}).get("backends", []):
            if backend.get("cluster") == cluster:
                return backend["name"]
        raise UpstreamUnavailable(
            "No Gateway backend is matched to cluster {}. TMS will not guess "
            "which backend to deactivate.".format(cluster))

    # ------------------------------------------------------------ read side

    def active(self) -> List[Dict[str, Any]]:
        try:
            return [s.as_dict() for s in self.repository.all_active()]
        except SequenceUnavailable:
            log.exception("cannot read active restart sequences")
            return []

    def get(self, principal: Principal, sequence_id: Any) -> Dict[str, Any]:
        stored = self.repository.load(sequence_id)
        if stored is None:
            raise NotFound("No such restart sequence: {}".format(sequence_id))
        self._observe(stored)
        payload = stored.as_dict()
        payload["steps"] = [
            {"state": state, "label": label, "status": status}
            for state, label, status in stored.sequence.steps()
        ]
        payload["executor"] = self.executor.describe(stored.sequence.cluster)
        payload["automated"] = self.executor.automated
        return payload

    def recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        try:
            return [s.as_dict() for s in self.repository.recent(limit)]
        except SequenceUnavailable:
            return []

    # ----------------------------------------------------------- write side

    def start(self, principal: Principal, cluster: str, reason: str) -> Dict[str, Any]:
        """Step 1. Stop new queries reaching the cluster."""
        self._require_admin(principal)
        self._cluster_or_404(cluster)
        if not (reason or "").strip():
            raise InvalidRequest("A reason is required to restart a cluster.")

        sequence = RestartSequence(cluster=cluster, reason=reason, actor=principal.username)
        try:
            stored = self.repository.create(sequence, roles=principal.roles)
        except ActiveSequenceExists as exc:
            raise InvalidRequest(str(exc))
        except SequenceUnavailable as exc:
            # Refuse rather than deactivate something TMS cannot track.
            raise UpstreamUnavailable(
                "Cannot record the restart, so TMS will not start one: {}".format(exc))

        try:
            with self._audited(principal, cluster, reason):
                self._set_gateway_active(cluster, False)
        except Exception as exc:  # noqa: BLE001
            # Traffic was never stopped, so there is nothing to restore - close
            # the sequence out rather than leaving a phantom active one.
            stored.sequence.log("Could not stop traffic: {}".format(exc), level="error")
            stored.sequence.begin_abort("Nothing was changed.")
            stored.sequence.finish_abort()
            self.repository.save(stored)
            raise

        stored.sequence.begin()
        self._observe(stored)
        self.repository.save(stored)
        return self.get(principal, stored.id)

    def refresh(self, principal: Principal, sequence_id: Any) -> Dict[str, Any]:
        """Re-observe without changing anything. Drives the live view."""
        stored = self.repository.load(sequence_id)
        if stored is None:
            raise NotFound("No such restart sequence: {}".format(sequence_id))
        before = len(stored.sequence.history)
        self._observe(stored)
        if len(stored.sequence.history) != before:
            self.repository.save(stored)
        return self.get(principal, sequence_id)

    def force_drain(self, principal: Principal, sequence_id: Any,
                    override_reason: str) -> Dict[str, Any]:
        self._require_admin(principal)
        stored = self._load_or_404(sequence_id)
        self._observe(stored)
        try:
            stored.sequence.force_drained(override_reason)
        except SequenceError as exc:
            raise InvalidRequest(str(exc))
        with self._audited(principal, stored.sequence.cluster, override_reason):
            self.repository.save(stored, force_reason=override_reason)
        return self.get(principal, sequence_id)

    def restart(self, principal: Principal, sequence_id: Any) -> Dict[str, Any]:
        """Step 4. Only reachable once the cluster is empty."""
        self._require_admin(principal)
        stored = self._load_or_404(sequence_id)
        self._observe(stored)
        try:
            stored.sequence.confirm_drained()
            stored.sequence.mark_restarting()
        except StepBlocked as exc:
            raise InvalidRequest(str(exc))
        except SequenceError as exc:
            raise InvalidRequest(str(exc))

        with self._audited(principal, stored.sequence.cluster, stored.sequence.reason):
            state = self.executor.start(stored.sequence.cluster, str(stored.id))
        if state == PENDING_OPERATOR:
            stored.sequence.log(
                "Waiting for the operator to restart {}.".format(stored.sequence.cluster))
        else:
            stored.sequence.log("Running the restart playbook.")
        self.repository.save(stored)
        return self.get(principal, sequence_id)

    def mark_restarted(self, principal: Principal, sequence_id: Any) -> Dict[str, Any]:
        self._require_admin(principal)
        stored = self._load_or_404(sequence_id)
        try:
            stored.sequence.mark_restarted()
        except SequenceError as exc:
            raise InvalidRequest(str(exc))
        self._observe(stored)
        self.repository.save(stored)
        return self.get(principal, sequence_id)

    def complete(self, principal: Principal, sequence_id: Any) -> Dict[str, Any]:
        """Steps 5 and 6. Health first, then traffic."""
        self._require_admin(principal)
        stored = self._load_or_404(sequence_id)
        self._observe(stored)
        try:
            stored.sequence.confirm_healthy()
        except StepBlocked as exc:
            self.repository.save(stored)
            raise InvalidRequest(str(exc))
        except SequenceError as exc:
            raise InvalidRequest(str(exc))

        with self._audited(principal, stored.sequence.cluster, stored.sequence.reason):
            self._set_gateway_active(stored.sequence.cluster, True)
        stored.sequence.complete()
        self.repository.save(stored)
        return self.get(principal, sequence_id)

    def abort(self, principal: Principal, sequence_id: Any,
              note: str = "") -> Dict[str, Any]:
        """Abort restores traffic. It is not "stop", it is "put it back"."""
        self._require_admin(principal)
        stored = self._load_or_404(sequence_id)
        try:
            stored.sequence.begin_abort(note or "")
        except SequenceError as exc:
            raise InvalidRequest(str(exc))
        self.repository.save(stored)

        try:
            with self._audited(principal, stored.sequence.cluster,
                               note or stored.sequence.reason):
                self._set_gateway_active(stored.sequence.cluster, True)
        except Exception as exc:  # noqa: BLE001
            # Leave the sequence in ABORTING: it is still holding traffic back
            # and must stay visible until someone fixes it.
            stored.sequence.log(
                "Could not restore traffic: {}. {} is still receiving no queries "
                "- reactivate it in the Gateway.".format(exc, stored.sequence.cluster),
                level="error")
            self.repository.save(stored)
            raise

        stored.sequence.finish_abort()
        self.repository.save(stored)
        return self.get(principal, sequence_id)

    def _load_or_404(self, sequence_id: Any):
        stored = self.repository.load(sequence_id)
        if stored is None:
            raise NotFound("No such restart sequence: {}".format(sequence_id))
        return stored
