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
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tms.api.errors import Forbidden, InvalidRequest, NotFound, UpstreamUnavailable
from tms.api.permissions import MANAGE_HEALTH, Principal
from tms.clients.errors import TrinoClientError
from tms.collector.snapshot import KIND_HEALTH, KIND_QUERIES
from tms.core.audit import ACTION_CLUSTER_RESTART, TARGET_CLUSTER, AuditGuard
from tms.ops.executor import FAILED, PENDING_OPERATOR, SUCCEEDED, UNKNOWN
from tms.ops.repository import ActiveSequenceExists, SequenceUnavailable
from tms.ops.sequence import (
    DRAINED,
    DRAINING,
    LEVEL_ERROR,
    LEVEL_OUTPUT,
    LEVEL_WARN,
    RESTARTING,
    VERIFYING,
    RestartSequence,
    SequenceError,
    StepBlocked,
)

log = logging.getLogger(__name__)


def _elapsed_ms(started_at, finished_at):
    """How long the restart has taken, or took.

    Counts to *now* while it is still running, so the header answers "how long
    has this cluster been out of rotation" - which is the question an operator
    watching a stalled drain is actually asking.
    """
    if not started_at:
        return None
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(finished_at) if finished_at else _now(start)
    except (TypeError, ValueError):
        return None
    return max(0.0, (end - start).total_seconds() * 1000.0)


def _now(reference):
    """Now, in the same awareness as the stored timestamp."""
    if reference.tzinfo is None:
        return datetime.now()
    return datetime.now(timezone.utc)


class RestartService:
    def __init__(self, config, repository, snapshots, gateway_client,
                 audit_guard: AuditGuard, executor, config_store=None) -> None:
        self.config = config
        self.repository = repository
        self.snapshots = snapshots
        self.gateway = gateway_client
        self.audit = audit_guard
        self.executor = executor
        # None when Trino still uses the file resource group manager, or when
        # the store could not be opened at startup. The sequence then has no
        # opinion rather than a wrong one.
        self.config_store = config_store

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

        self._observe_config_store(stored)

    def _observe_config_store(self, stored) -> None:
        """Would this cluster be able to start again? (D-010)

        Only while draining or drained. Before that the answer is not yet
        actionable, and after the restart the coordinator has already proved it
        by coming up - re-asking would add a database round trip per refresh to
        answer a question that has been settled.

        Deliberately checked *during* the drain rather than only at the button:
        a drain takes minutes, and knowing early that the store is down is the
        difference between fixing it in parallel and discovering it with the
        cluster already out of rotation.
        """
        if self.config_store is None:
            return
        if stored.sequence.state not in (DRAINING, DRAINED):
            return
        try:
            cluster = self.config.cluster(stored.sequence.cluster)
        except KeyError:
            return
        probe = self.config_store.probe(cluster.node_environment)
        stored.sequence.observe_config_store(probe.ready, probe.detail)

    def _poll_executor(self, stored) -> None:
        """Pull an automated restart's progress into the sequence log.

        Only meaningful while the restart is actually running. The executor
        streams `ansible-playbook` output, and this is what moves those lines
        into the record the operator is watching - so the right-hand panel is
        the real thing happening, not a spinner.

        ⛔ A failed playbook does not advance the sequence and does not abort
        it. Traffic stays stopped and the decision stays with the operator:
        retrying and giving up are different choices and TMS does not make
        either one on their behalf.
        """
        if stored.sequence.state != RESTARTING or not self.executor.automated:
            return

        sequence_id = str(stored.id)
        cluster = stored.sequence.cluster

        lines_since = getattr(self.executor, "lines_since", None)
        if lines_since is not None:
            # Counted from the stored history rather than held in memory: the
            # live view loads the sequence fresh on every poll, so an in-memory
            # cursor would restart at zero each time and record the playbook
            # output again, and again.
            shown = sum(1 for event in stored.sequence.history
                        if event.get("level") == LEVEL_OUTPUT)
            for line in lines_since(sequence_id, shown):
                stored.sequence.log(line, level=LEVEL_OUTPUT)

        state = self.executor.status(cluster, sequence_id)
        if state == SUCCEEDED:
            stored.sequence.log("Playbook finished successfully.")
            stored.sequence.mark_restarted()
        elif state == FAILED:
            detail = ""
            result = getattr(self.executor, "result", None)
            if result is not None:
                detail = (result(sequence_id) or {}).get("error") or ""
            # The advice has to name a button that exists. Re-running the
            # restart is refused from RESTARTING, so telling someone to "run it
            # again" sends them looking for a control that is not there.
            stored.sequence.log(
                "The restart playbook failed{}. {} is drained and still "
                "receiving no queries — nothing was restarted. Abort to put it "
                "back in rotation, then start again once the cause is "
                "fixed.".format(": " + detail if detail else "", cluster),
                level=LEVEL_ERROR)
        elif state == UNKNOWN:
            # TMS was restarted while the playbook ran. Saying so beats
            # guessing: a wrong guess here restores traffic to a cluster that
            # may not be back.
            stored.sequence.log(
                "TMS lost sight of the restart of {} (it was restarted while "
                "the playbook was running). Check the cluster yourself before "
                "continuing.".format(cluster), level=LEVEL_WARN)

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

    @staticmethod
    def _fingerprint(stored):
        """What the UI would notice changing. Compared to decide whether the
        observation is worth a write - the live view polls every couple of
        seconds and most polls see nothing new."""
        sequence = stored.sequence
        return (sequence.state, sequence.running_queries, sequence.health_state,
                len(sequence.history))

    def _payload(self, stored) -> Dict[str, Any]:
        payload = stored.as_dict()
        payload["steps"] = [
            {"state": state, "label": label, "status": status, "number": index + 1}
            for index, (state, label, status) in enumerate(stored.sequence.steps())
        ]
        payload["executor"] = self.executor.describe(stored.sequence.cluster)
        payload["automated"] = self.executor.automated
        # So the screen can stop claiming the playbook is running after it has
        # failed. Only meaningful while the restart step is in flight.
        payload["executor_state"] = (
            self.executor.status(stored.sequence.cluster, str(stored.id))
            if stored.sequence.state == RESTARTING and self.executor.automated
            else None)
        payload["duration_ms"] = _elapsed_ms(stored.started_at, stored.finished_at)
        return payload

    def get(self, principal: Principal, sequence_id: Any) -> Dict[str, Any]:
        stored = self.repository.load(sequence_id)
        if stored is None:
            raise NotFound("No such restart sequence: {}".format(sequence_id))
        self._observe(stored)
        return self._payload(stored)

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
        """Re-observe and drive the live view.

        The operator takes no action here, but TMS may still move: a finished
        playbook advances the sequence, and its output is pulled into the log.
        That is the whole point of an automated restart - the screen keeps up
        with the work without anyone clicking anything.
        """
        stored = self.repository.load(sequence_id)
        if stored is None:
            raise NotFound("No such restart sequence: {}".format(sequence_id))
        before = self._fingerprint(stored)
        self._observe(stored)
        self._poll_executor(stored)
        if self._fingerprint(stored) != before:
            # Only when something actually changed: this runs every couple of
            # seconds for every operator watching, and most polls see nothing.
            self.repository.save(stored)
        return self._payload(stored)

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
            # Says what to do, not just what TMS is doing. "Waiting for the
            # operator" was read as TMS working on something.
            stored.sequence.log(
                "TMS is NOT restarting {0} — restart it yourself now, then "
                "confirm below. Nothing is happening automatically.".format(
                    stored.sequence.cluster), level=LEVEL_WARN)
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
                level=LEVEL_ERROR)
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
