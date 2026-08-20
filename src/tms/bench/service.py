"""The benchmark harness's rules (FR-BM-01/03/04).

Absolute rule 3 applies here in full, unlike the work board: starting a run
consumes a real cluster's capacity on somebody's say-so. Reason, audit record,
administrator.

Absolute rule 5 shapes what this service refuses to do. It never deactivates a
backend - see `guard.py`. The operator excludes the cluster and TMS checks
their work, because a "run benchmark" button that could take a cluster out of
rotation is the independent deactivate toggle CLAUDE.md forbids, renamed.

Python 3.9 compatible.
"""

import logging
from typing import Any, Dict, List, Optional

from tms.api.errors import (
    AuditUnavailableError,
    Forbidden,
    InvalidRequest,
    NotFound,
    ReasonRequiredError,
    UpstreamUnavailable,
)
from tms.api.permissions import MANAGE_HEALTH, VIEW_HEALTH, Principal
from tms.bench import guard as guards
from tms.bench.compare import NotComparable, compare
from tms.bench.runner import RUNNING
from tms.bench.store import ActiveRunExists, BenchmarkStoreUnavailable
from tms.core.audit import (
    ACTION_BENCHMARK_RUN,
    TARGET_CLUSTER,
    AuditGuard,
    AuditUnavailable,
    ReasonRequired,
)

log = logging.getLogger(__name__)


class BenchmarkService:
    def __init__(self, config, snapshots, audit_guard: AuditGuard, repository,
                 runner, query_sets: Dict[str, Any], gateway_client=None,
                 stale_threshold: float = 120.0) -> None:
        self.config = config
        self.snapshots = snapshots
        self.audit = audit_guard
        self.repository = repository
        self.runner = runner
        self.query_sets = query_sets
        self.gateway = gateway_client
        self._stale_threshold = stale_threshold

    # ------------------------------------------------------------- guards

    def _require_view(self, principal: Principal) -> None:
        if not principal.can(VIEW_HEALTH):
            raise Forbidden("You do not have permission to view benchmarks.")

    def _require_admin(self, principal: Principal) -> None:
        if not principal.can(MANAGE_HEALTH):
            raise Forbidden("Only administrators can start a benchmark.")

    def _cluster_or_404(self, cluster: str):
        try:
            return self.config.cluster(cluster)
        except KeyError:
            raise NotFound("No such cluster: {}".format(cluster))

    def _set_or_404(self, key: str):
        found = self.query_sets.get(key)
        if found is None:
            raise NotFound(
                "No query set named {!r}. Sets are declared in "
                "benchmark.query_sets.".format(key))
        return found

    def guard_for(self, cluster: str) -> guards.GuardResult:
        """FR-BM-04, as data. Shown before anyone presses anything."""
        return guards.check(cluster, self.gateway, self.snapshots,
                            self._stale_threshold)

    # ------------------------------------------------------------ reading

    def overview(self, principal: Principal, cluster: str) -> Dict[str, Any]:
        self._require_view(principal)
        self._cluster_or_404(cluster)
        guard = self.guard_for(cluster)
        try:
            recent = self.repository.recent(limit=20, cluster=cluster)
            active = self.repository.active(cluster=cluster)
        except BenchmarkStoreUnavailable as exc:
            log.warning("benchmark store unavailable: %s", exc)
            recent, active = [], []
        return {
            "cluster": cluster,
            "query_sets": [s.as_dict() for s in self.query_sets.values()],
            "guard": guard.as_dict(),
            "runs": recent,
            "active": active,
            "can_start": principal.can(MANAGE_HEALTH),
            "max_repetitions": _max_repetitions(self.config),
        }

    def run(self, principal: Principal, run_id: Any) -> Dict[str, Any]:
        self._require_view(principal)
        try:
            found = self.repository.get(run_id)
        except BenchmarkStoreUnavailable as exc:
            raise UpstreamUnavailable(str(exc))
        if found is None:
            raise NotFound("No such benchmark run: {}".format(run_id))
        return found

    def comparable_runs(self, principal: Principal, run: Dict[str, Any],
                        limit: int = 20) -> List[Dict[str, Any]]:
        """Other finished runs of the same set - what may go on the other side.

        Filtered here rather than in the template: offering a run of a
        different set as a comparison choice is offering a wrong answer.
        """
        self._require_view(principal)
        try:
            candidates = self.repository.recent(
                limit=limit + 1, query_set=run.get("query_set"))
        except BenchmarkStoreUnavailable:
            return []
        return [r for r in candidates
                if str(r["id"]) != str(run["id"]) and r["state"] != RUNNING][:limit]

    def compare(self, principal: Principal, baseline_id: Any,
                candidate_id: Any) -> Dict[str, Any]:
        self._require_view(principal)
        baseline = self.run(principal, baseline_id)
        candidate = self.run(principal, candidate_id)
        try:
            return compare(baseline, candidate)
        except NotComparable as exc:
            raise InvalidRequest(str(exc))

    # ------------------------------------------------------------ writing

    def start(self, principal: Principal, cluster: str, query_set: str,
              reason: str, repetitions: int = 1,
              label: Optional[str] = None) -> Dict[str, Any]:
        self._require_admin(principal)
        self._cluster_or_404(cluster)
        declared = self._set_or_404(query_set)

        ceiling = _max_repetitions(self.config)
        try:
            repetitions = int(repetitions)
        except (TypeError, ValueError):
            raise InvalidRequest("Repetitions must be a number.")
        if not 1 <= repetitions <= ceiling:
            raise InvalidRequest(
                "Repetitions must be between 1 and {}.".format(ceiling))

        # ⛔ FR-BM-04. Checked here, before the audit record, so a refused run
        # is not written down as an action that happened.
        guard = self.guard_for(cluster)
        if not guard.ok:
            raise InvalidRequest(guard.summary())

        try:
            return self._start_audited(principal, cluster, declared, reason,
                                       repetitions, label, guard)
        except ReasonRequired as exc:
            raise ReasonRequiredError(str(exc))
        except AuditUnavailable as exc:
            raise AuditUnavailableError(str(exc))

    def _start_audited(self, principal, cluster, declared, reason, repetitions,
                       label, guard):
        with self.audit.action(
            actor=principal.username, roles=principal.roles,
            action_type=ACTION_BENCHMARK_RUN, target_kind=TARGET_CLUSTER,
            target_id="{}:{}".format(cluster, declared.key), target_cluster=cluster,
            reason=reason, actor_ip=principal.ip,
        ):
            try:
                run = self.repository.create(
                    cluster=cluster, query_set=declared.key,
                    actor=principal.username, roles=principal.roles,
                    reason=reason, repetitions=repetitions,
                    guard=guard.as_dict(), label=(label or None))
            except ActiveRunExists:
                raise InvalidRequest(
                    "A benchmark is already running on {}. Two runs on one "
                    "cluster measure each other.".format(cluster))
            except BenchmarkStoreUnavailable as exc:
                # A measurement TMS cannot record is a cluster taken out of
                # rotation for nothing.
                raise UpstreamUnavailable(
                    "Cannot record this run, so it will not be started: "
                    "{}".format(exc))

            self.runner.start(run, declared, repetitions)
            log.info("benchmark %s started on %s by %s (%d x %d queries)",
                     run["id"], cluster, principal.username, repetitions,
                     len(declared.queries))
            return run

    def abort(self, principal: Principal, run_id: Any) -> Dict[str, Any]:
        """Stop after the query in flight.

        No reason and no audit record, deliberately: stopping is the *absence*
        of further action against the cluster, and the run row already carries
        who started it and why. Demanding a second justification to stop doing
        something makes the safe choice the expensive one.
        """
        self._require_admin(principal)
        run = self.run(principal, run_id)
        if run["state"] != RUNNING:
            raise InvalidRequest("That run has already finished.")
        self.runner.abort(run["id"])
        return run


def _max_repetitions(config) -> int:
    from tms.bench.queryset import MAX_REPETITIONS

    declared = getattr(getattr(config, "benchmark", None), "max_repetitions", None)
    return int(declared or MAX_REPETITIONS)
