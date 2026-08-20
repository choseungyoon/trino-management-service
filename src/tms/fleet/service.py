"""Reading the fleet and taking a worker out of it (FR-FL-01, FR-FL-03).

Graceful shutdown is deliberately *not* built as a multi-step sequence like the
cluster restart. It does not need one: Trino drains the worker itself, the
cluster stays up throughout, and there is no traffic to stop at the Gateway.
Wrapping it in a six-step ceremony would imply a danger that is not there, and
ceremony that is not earned gets clicked through.

What it does need, and has:

* a reason, an audit record and an administrator (CLAUDE.md rule 3),
* a refusal to shut down a coordinator,
* honesty about how long it takes.

⛔ Why the coordinator is refused
--------------------------------
There is no coordinator HA. Shutting the coordinator down does not drain a
node, it ends the cluster - every running query dies with it. That is the
restart sequence's job, where traffic is stopped first. A "shutdown" button
that also happened to work on coordinators would be a path around CLAUDE.md
rule 5, so this one refuses by role.

Python 3.9 compatible.
"""

import logging
from typing import Any, Dict, List, Optional

from tms.api.errors import Forbidden, InvalidRequest, NotFound, UpstreamUnavailable
from tms.api.permissions import MANAGE_HEALTH, Principal
from tms.clients.errors import TrinoClientError
from tms.clients.node import NodeClient
from tms.collector.snapshot import KIND_FLEET
from tms.core.audit import (
    ACTION_FLEET_JOB,
    ACTION_NODE_SHUTDOWN,
    TARGET_CLUSTER,
    TARGET_NODE,
    AuditGuard,
)

log = logging.getLogger(__name__)


class FleetService:
    def __init__(self, config, snapshots, audit_guard: AuditGuard,
                 transport_factory, stale_threshold: float = 120.0,
                 job_runner=None, job_repository=None, sql_client_factory=None) -> None:
        self.config = config
        self.snapshots = snapshots
        self.audit = audit_guard
        self._transport_factory = transport_factory
        self._stale_threshold = stale_threshold
        # Both None unless `fleet.jobs` declares something. The screen then
        # shows no job panel at all rather than an empty one - a control that
        # can only say "nothing configured" is noise.
        self.job_runner = job_runner
        self.job_repository = job_repository
        # FR-FL-02. None until the ExecuteQuery grant is in place (D-012); the
        # screen then says the answer is unavailable rather than offering a
        # button that cannot work.
        self._sql_client_factory = sql_client_factory

    def _require_view(self, principal: Principal) -> None:
        from tms.api.permissions import VIEW_HEALTH
        from tms.api.services import require

        require(principal, VIEW_HEALTH)

    # ----------------------------------------------------------- FR-FL-02

    @property
    def discovery_lookup_available(self) -> bool:
        return self._sql_client_factory is not None

    def identify_unjoined(self, principal: Principal, cluster: str) -> Dict[str, Any]:
        """Which inventory node is not in the coordinator's node list.

        ⛔ On demand only. This is the exception principle A1 was narrowed to
        allow (D-012), and the narrowing holds only while these queries stay
        rare - so nothing calls it on a timer, and the screen offers it only
        when the counts already disagree.

        A read, so no `reason` and no audit row: rule 3 governs writes. It is
        logged, because "why did TMS run a query" is a question worth being
        able to answer even when the answer is boring.
        """
        from tms.fleet.discovery import identify

        self._require_view(principal)
        self._cluster_or_404(cluster)
        if not self.discovery_lookup_available:
            raise InvalidRequest(
                "TMS cannot query the coordinator's node list. This needs the "
                "ExecuteQuery permission on the TMS account (D-012).")

        snapshot = self.snapshots.load(cluster, KIND_FLEET)
        inventory = ((snapshot.payload if snapshot else None) or {}).get("nodes") or []
        log.info("discovery lookup on %s requested by %s", cluster, principal.username)
        result = identify(self._sql_client_factory(cluster), inventory)
        result["cluster"] = cluster
        return result

    # ------------------------------------------------------- FR-FL-04/05

    @property
    def jobs_enabled(self) -> bool:
        return self.job_runner is not None and self.job_repository is not None

    def list_jobs(self, principal: Principal, cluster: str) -> Dict[str, Any]:
        """What can be run here, and what has been."""
        self._require_view(principal)
        self._cluster_or_404(cluster)
        if not self.jobs_enabled:
            return {"enabled": False, "definitions": [], "runs": [], "active": None}
        definitions = [
            {"key": job.key, "title": job.title, "description": job.description,
             "parameters": [{"name": p.name, "label": p.label, "min": p.minimum,
                             "max": p.maximum, "default": p.default}
                            for p in job.parameters]}
            for job in sorted(self.job_runner.jobs.values(), key=lambda j: j.key)
        ]
        active = self.job_repository.active(cluster=cluster)
        return {
            "enabled": True,
            "definitions": definitions,
            "runs": self.job_repository.recent(limit=20, cluster=cluster),
            "active": active[0] if active else None,
        }

    def get_job_run(self, principal: Principal, run_id) -> Dict[str, Any]:
        self._require_view(principal)
        if not self.jobs_enabled:
            raise NotFound("Fleet jobs are not configured.")
        run = self.job_repository.get(run_id)
        if run is None:
            raise NotFound("No such job run: {}".format(run_id))
        return run

    def start_job(self, principal: Principal, cluster: str, key: str,
                  parameters: Dict[str, Any], reason: str) -> Dict[str, Any]:
        """Run a configured playbook against one cluster (FR-FL-04).

        ⛔ This is not the safe restart sequence and cannot stand in for it.
        TMS sees a path and an exit code; it has no idea whether the playbook
        drains anything. Pointing a job at a restart would be a way around
        CLAUDE.md rule 5, which is why `tms-config-check` refuses that case and
        why the config comment says so twice.
        """
        from tms.api.errors import AuditUnavailableError
        from tms.core.audit import AuditUnavailable
        from tms.fleet.jobs import JobError
        from tms.fleet.jobstore import ActiveJobExists, JobStoreUnavailable

        if not principal.can(MANAGE_HEALTH):
            raise Forbidden("You do not have permission to run fleet jobs.")
        self._cluster_or_404(cluster)
        if not (reason or "").strip():
            raise InvalidRequest("A reason is required to run a job.")
        if not self.jobs_enabled:
            raise InvalidRequest("Fleet jobs are not configured (fleet.jobs).")

        try:
            definition = self.job_runner.definition(key)
            cleaned = definition.clean(parameters or {})
        except JobError as exc:
            raise InvalidRequest(str(exc))

        # ⛔ AuditUnavailable is not an ApiError, so an unconverted one leaves
        # the route as a 500 - "internal server error" where the truth is "the
        # audit store is down, so nothing was started". Same fix as
        # api/services.py._rg_write.
        try:
            return self._start_job_audited(principal, cluster, key, cleaned, reason)
        except AuditUnavailable as exc:
            raise AuditUnavailableError(str(exc))

    def _start_job_audited(self, principal, cluster, key, cleaned, reason):
        from tms.api.errors import InvalidRequest
        from tms.fleet.jobs import JobError
        from tms.fleet.jobstore import ActiveJobExists, JobStoreUnavailable

        with self.audit.action(
            actor=principal.username, roles=principal.roles,
            action_type=ACTION_FLEET_JOB, target_kind=TARGET_CLUSTER,
            target_id="{}:{}".format(cluster, key), target_cluster=cluster,
            reason=reason, actor_ip=principal.ip,
        ):
            try:
                run = self.job_repository.create(
                    cluster=cluster, job=key, actor=principal.username,
                    roles=principal.roles, reason=reason, parameters=cleaned)
            except ActiveJobExists:
                raise InvalidRequest(
                    "A job is already running on {}. Two playbooks writing the "
                    "same inventory at once is not a conflict anyone can "
                    "untangle afterwards.".format(cluster))
            except JobStoreUnavailable as exc:
                # Same rule as the restart sequence: a change TMS cannot record
                # is a change nobody will be able to explain later.
                raise UpstreamUnavailable(
                    "Cannot record this job, so it will not be started: "
                    "{}".format(exc))

            run_id = run["id"]
            store = self.job_repository

            def on_line(line: str) -> None:
                store.append_output(run_id, line)

            def on_finish(result: Dict[str, Any]) -> None:
                store.finish(run_id, result.get("state"),
                             exit_code=result.get("exit_code"),
                             error=result.get("error"))

            try:
                command = self.job_runner.start(
                    key, cluster, cleaned, on_line, on_finish)
            except JobError as exc:
                store.finish(run_id, "FAILED", error=str(exc))
                raise InvalidRequest(str(exc))

        log.info("fleet job %s started on %s by %s", key, cluster, principal.username)
        store.append_output(run_id, "Running {}".format(" ".join(command)), level="info")
        return dict(run, id=run_id)

    # ------------------------------------------------------------------ read

    def get_fleet(self, principal: Principal, cluster: str) -> Dict[str, Any]:
        from tms.api.services import envelope, require
        from tms.api.permissions import VIEW_HEALTH

        require(principal, VIEW_HEALTH)
        self._cluster_or_404(cluster)
        enabled = self.config.fleet.enabled
        snapshot = self.snapshots.load(cluster, KIND_FLEET)
        if snapshot is None:
            return envelope(
                None,
                {"nodes": [], "summary": {}, "notes": [], "enabled": enabled,
                 "unavailable_reason": (
                     None if enabled else
                     "Fleet collection is off (fleet.enabled).")},
                self._stale_threshold,
            )
        payload = dict(snapshot.payload or {})
        payload["enabled"] = enabled
        if snapshot.collection_error:
            payload["unavailable_reason"] = snapshot.collection_error
            payload["advice"] = snapshot.advice
        payload["limits"] = self._limits(payload)
        return envelope(snapshot, payload, self._stale_threshold)

    @staticmethod
    def _limits(payload: Dict[str, Any]) -> List[str]:
        """What this screen cannot tell you, and why.

        Stated on the screen rather than left for someone to infer from an
        absent column. A monitoring screen that quietly omits a fact is read as
        that fact being fine.
        """
        limits = [
            "Discovery join status per node needs `system.runtime.nodes`, which "
            "requires the ExecuteQuery permission TMS does not hold. The "
            "coordinator's node counts below are the cross-check TMS can make.",
        ]
        counts = payload.get("node_counts") or {}
        active = counts.get("ActiveNodeCount")
        expected = payload.get("inventory_size")
        if active is not None and expected:
            limits.append(
                "The inventory lists {} node(s); the coordinator counts {} "
                "active.".format(expected, active))
        return limits

    # ----------------------------------------------------------------- write

    def shutdown_node(self, principal: Principal, cluster: str, host: str,
                      reason: str) -> Dict[str, Any]:
        """Ask one worker to drain and exit (FR-FL-03)."""
        if not principal.can(MANAGE_HEALTH):
            raise Forbidden("You do not have permission to shut down a node.")
        self._cluster_or_404(cluster)
        if not (reason or "").strip():
            raise InvalidRequest("A reason is required to shut down a node.")

        node = self._node_or_404(cluster, host)
        if node.get("role") != "worker":
            # Not a permission problem, a correctness one: see the module note.
            raise InvalidRequest(
                "{} is the coordinator. Shutting it down ends the cluster and "
                "kills every running query — it does not drain a node. Use the "
                "restart sequence, which stops traffic first.".format(host))
        if node.get("state") == "SHUTTING_DOWN":
            raise InvalidRequest("{} is already shutting down.".format(host))

        url = self._url_for(node)
        with self.audit.action(
            actor=principal.username, roles=principal.roles,
            action_type=ACTION_NODE_SHUTDOWN, target_kind=TARGET_NODE,
            target_id=host, target_cluster=cluster, reason=reason,
            actor_ip=principal.ip,
        ):
            client = NodeClient(
                url, self._transport_factory(),
                user=self.config.trino.user,
                password=self.config.trino.password.reveal(),
                verify_tls=self.config.trino.verify_tls,
                write_timeout=self.config.trino.write_timeout_seconds,
            )
            try:
                client.begin_shutdown()
            except TrinoClientError as exc:
                raise UpstreamUnavailable(str(exc))

        return {
            "host": host,
            "cluster": cluster,
            "accepted": True,
            # Said back explicitly, because the node will still be listed as
            # SHUTTING_DOWN for minutes and that looks stuck otherwise.
            "note": (
                "{} is draining. Trino waits one grace period, finishes its "
                "running tasks, waits another grace period, then exits — at "
                "least twice `shutdown.grace-period` (four minutes on the "
                "defaults). It stays listed until it goes.".format(host)),
        }

    # ----------------------------------------------------------------- inner

    def _cluster_or_404(self, cluster: str):
        try:
            return self.config.cluster(cluster)
        except KeyError:
            raise NotFound("Unknown cluster: {}".format(cluster))

    def _node_or_404(self, cluster: str, host: str) -> Dict[str, Any]:
        snapshot = self.snapshots.load(cluster, KIND_FLEET)
        for node in ((snapshot.payload if snapshot else {}) or {}).get("nodes", []):
            if node.get("host") == host:
                return node
        raise NotFound(
            "{} is not in the inventory for {}. TMS will not send a shutdown to "
            "a host it does not know.".format(host, cluster))

    def _url_for(self, node: Dict[str, Any]) -> str:
        template = self.config.fleet.node_url_template
        # The address comes from the inventory file, never from the request:
        # the request only names a host that must already be in that file.
        return template.format(address=node.get("address") or node.get("host"),
                               host=node.get("host"))
