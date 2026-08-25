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
from tms.bench.queryset import MAX_QUERIES, refuse_name, refuse_statement
from tms.bench.runner import RUNNING
from tms.bench.setstore import (
    DuplicateName,
    InMemoryQuerySetRepository,
    QuerySetStoreUnavailable,
    UnknownSet,
)
from tms.bench.store import ActiveRunExists, BenchmarkStoreUnavailable
from tms.core.audit import (
    ACTION_BENCHMARK_QUERY_CHANGE,
    ACTION_BENCHMARK_RUN,
    TARGET_BENCHMARK_SET,
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
        # A plain dict is a legitimate read-only repository - it has get() and
        # values() - and tests and the demo pass one. Wrapping it here means
        # the write paths below do not each need to ask which kind they got.
        self.query_sets = (InMemoryQuerySetRepository(query_sets)
                           if isinstance(query_sets, dict) else query_sets)
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
        try:
            found = self.query_sets.get(key)
        except QuerySetStoreUnavailable as exc:
            raise UpstreamUnavailable(str(exc))
        if found is None:
            raise NotFound(
                "No query set named {!r}. Sets are created on the query set "
                "page.".format(key))
        return found

    def _all_sets(self) -> List[Any]:
        """Every set, or an empty list if storage is down.

        Tolerated rather than raised: the benchmark page's other half is the
        run history, and a database blip should not replace the whole screen
        with an error when part of it still has something to say.
        """
        try:
            return list(self.query_sets.values())
        except QuerySetStoreUnavailable as exc:
            log.warning("query set store unavailable: %s", exc)
            return []

    def _not_while_running(self, key: str) -> None:
        """⛔ Refuse to edit a set that is being executed right now.

        The runner reads the statements once, at start, so an edit mid-run
        would not change what executes - it would change what the set *says*
        executed, which is worse: the run would finish and record numbers
        against text that never ran.
        """
        try:
            active = self.repository.active()
        except BenchmarkStoreUnavailable:
            active = []
        clusters = [r["cluster"] for r in active if r.get("query_set") == key]
        if clusters:
            raise InvalidRequest(
                "A benchmark of {!r} is running on {}. Wait for it to finish "
                "or abort it first.".format(key, ", ".join(sorted(clusters))))

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
            "query_sets": [s.as_dict() for s in self._all_sets()],
            "guard": guard.as_dict(),
            "runs": recent,
            "active": active,
            "can_start": principal.can(MANAGE_HEALTH),
            "can_edit": principal.can(MANAGE_HEALTH),
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

    def sets(self, principal: Principal) -> Dict[str, Any]:
        """Every set, with how many runs each has behind it."""
        self._require_view(principal)
        try:
            counted = {r["query_set"] for r in self.repository.recent(limit=200)}
        except BenchmarkStoreUnavailable:
            counted = set()
        return {
            "sets": [dict(s.as_dict(), has_runs=s.key in counted)
                     for s in self._all_sets()],
            "can_edit": principal.can(MANAGE_HEALTH),
            "max_queries": MAX_QUERIES,
        }

    def query_set(self, principal: Principal, key: str) -> Dict[str, Any]:
        self._require_view(principal)
        found = self._set_or_404(key)
        try:
            runs = self.repository.recent(limit=10, query_set=key)
        except BenchmarkStoreUnavailable:
            runs = []
        return {
            "set": found.as_dict(),
            "runs": runs,
            "can_edit": principal.can(MANAGE_HEALTH),
            "max_queries": MAX_QUERIES,
        }

    def query_history(self, principal: Principal, key: str, name: str,
                      limit: int = 100) -> Dict[str, Any]:
        """Every execution of one query, and the statement each run used.

        The two are separate on purpose. `current` is what the query says
        today; `statement` on each row is what that run actually executed,
        read from the run's own snapshot. When they differ, the numbers above
        and below the change are not measurements of the same thing, and this
        page is the only place that would ever show it.
        """
        self._require_view(principal)
        found = self._set_or_404(key)
        current = next((q for q in found.queries if q.name == name), None)
        if current is None:
            raise NotFound(
                "No query named {!r} in {!r}.".format(name, key))
        try:
            rows = self.repository.history_for_query(key, name, limit=limit)
        except BenchmarkStoreUnavailable as exc:
            raise UpstreamUnavailable(str(exc))

        statements = {}
        for row in rows:
            run_id = row.get("run_id")
            if run_id not in statements:
                statements[run_id] = _statement_in(
                    self._run_snapshot(run_id), name)
            row["statement"] = statements[run_id]
            row["differs"] = (row["statement"] is not None
                              and row["statement"] != current.sql)
        return {
            "set": found.as_dict(),
            "query": current.as_dict(),
            "history": rows,
            "changed": any(r["differs"] for r in rows),
        }

    def _run_snapshot(self, run_id) -> List[Dict[str, Any]]:
        try:
            found = self.repository.get(run_id)
        except BenchmarkStoreUnavailable:
            return []
        return list((found or {}).get("queries") or [])

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
                    guard=guard.as_dict(), label=(label or None),
                    queries=[q.as_dict() for q in declared.queries])
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


    # ------------------------------------------------- editing (FR-BM-06)

    def save_set(self, principal: Principal, key: str, title: str,
                 description: str, reason: str) -> Dict[str, Any]:
        self._require_admin(principal)
        refusal = refuse_name(key, "set key")
        if refusal:
            raise InvalidRequest(refusal)
        self._not_while_running(key)
        return self._audited(
            principal, key, reason,
            lambda: self.query_sets.save_set(
                key=key, title=(title or "").strip(),
                description=(description or "").strip(),
                actor=principal.username)).as_dict()

    def delete_set(self, principal: Principal, key: str, reason: str) -> None:
        self._require_admin(principal)
        self._set_or_404(key)
        self._not_while_running(key)
        self._audited(principal, key, reason,
                      lambda: self.query_sets.delete_set(key))

    def save_query(self, principal: Principal, key: str, name: str, title: str,
                   statement: str, reason: str, position: int = 0,
                   original_name: Optional[str] = None) -> Dict[str, Any]:
        self._require_admin(principal)
        found = self._set_or_404(key)

        refusal = refuse_name(name, "query name")
        if refusal:
            raise InvalidRequest(refusal)

        # ⛔ The allowlist. See queryset.py's header: with sets out of git this
        # is the review step, and the runner checks it again before executing.
        statement = (statement or "").strip().rstrip(";")
        refusal = refuse_statement(statement)
        if refusal:
            raise InvalidRequest("That statement cannot be benchmarked: {}."
                                 .format(refusal))

        adding = original_name is None or original_name != name
        if adding and len(found.queries) >= MAX_QUERIES:
            raise InvalidRequest(
                "{!r} already holds {} queries, the most a set may have."
                .format(key, MAX_QUERIES))

        self._not_while_running(key)
        try:
            saved = self._audited(
                principal, "{}.{}".format(key, name), reason,
                lambda: self.query_sets.save_query(
                    set_key=key, name=name, title=(title or "").strip(),
                    statement=statement, position=int(position or 0),
                    actor=principal.username, original_name=original_name))
        except DuplicateName:
            raise InvalidRequest(
                "{!r} already has a query named {!r}. Results are keyed by "
                "name, so two would merge into one column.".format(key, name))
        except UnknownSet:
            raise NotFound("No query set named {!r}.".format(key))
        return saved.as_dict()

    def delete_query(self, principal: Principal, key: str, name: str,
                     reason: str) -> None:
        self._require_admin(principal)
        self._set_or_404(key)
        self._not_while_running(key)
        try:
            removed = self._audited(
                principal, "{}.{}".format(key, name), reason,
                lambda: self.query_sets.delete_query(key, name))
        except UnknownSet:
            raise NotFound("No query set named {!r}.".format(key))
        if not removed:
            raise NotFound("No query named {!r} in {!r}.".format(name, key))

    def _audited(self, principal: Principal, target: str, reason: str, write):
        """Rule 3 around one edit: reason, audit record, administrator.

        The audit row carries who, when and why. What the statement said before
        is not copied here - `benchmark_run.queries` already holds every
        version that was ever executed, which is the version anyone asking the
        question actually needs.
        """
        try:
            with self.audit.action(
                actor=principal.username, roles=principal.roles,
                action_type=ACTION_BENCHMARK_QUERY_CHANGE,
                target_kind=TARGET_BENCHMARK_SET, target_id=target,
                reason=reason, actor_ip=principal.ip,
            ):
                try:
                    return write()
                except QuerySetStoreUnavailable as exc:
                    raise UpstreamUnavailable(str(exc))
        except ReasonRequired as exc:
            raise ReasonRequiredError(str(exc))
        except AuditUnavailable as exc:
            raise AuditUnavailableError(str(exc))


def _statement_in(snapshot: List[Dict[str, Any]], name: str) -> Optional[str]:
    """The SQL a run recorded for one query, or None if it kept no snapshot.

    None is the honest answer for runs from before 018, whose `queries` column
    defaulted to empty. Showing today's text for them would be a guess.
    """
    for entry in snapshot or []:
        if entry.get("name") == name:
            return entry.get("sql")
    return None


def _max_repetitions(config) -> int:
    from tms.bench.queryset import MAX_REPETITIONS

    declared = getattr(getattr(config, "benchmark", None), "max_repetitions", None)
    return int(declared or MAX_REPETITIONS)
