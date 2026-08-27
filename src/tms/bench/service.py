"""What the benchmark harness will and will not do.

Starting a run consumes a real cluster's capacity, so it needs a reason, an
audit record, and an administrator.

⛔ It never takes a cluster out of rotation. Stopping intake belongs to the
safe restart sequence, which drains queries first; a benchmark button that
could deactivate a backend would be that sequence's shortcut under another
name. Whether the cluster was quiet is recorded instead - see guard.py.

Python 3.9 compatible.
"""

import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tms.api.errors import (
    ApiError,
    AuditUnavailableError,
    Forbidden,
    InvalidRequest,
    NotFound,
    ReasonRequiredError,
    UpstreamUnavailable,
)
from tms.api.permissions import (
    MANAGE_HEALTH, ROLE_ADMIN, VIEW_HEALTH, Principal)
from tms.bench import guard as guards
from tms.bench.compare import NotComparable, compare, query_rows
from tms.bench.queryset import MAX_QUERIES, refuse_name, refuse_statement
from tms.bench import schedules as scheduling
from tms.bench import trend
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
    ACTION_BENCHMARK_SCHEDULE_CHANGE,
    TARGET_BENCHMARK_SET,
    TARGET_CLUSTER,
    AuditGuard,
    AuditUnavailable,
    ReasonRequired,
)

log = logging.getLogger(__name__)


class BenchmarkAlreadyRunning(InvalidRequest):
    """A run is already going on this cluster.

    Still a 400 over HTTP - nothing about the request is malformed, the timing
    is. It has its own type so the scheduler can tell "the guard did its job"
    from "this schedule is broken": counting a skip as a failure would pause a
    schedule for behaving correctly.
    """


class BenchmarkService:
    def __init__(self, config, snapshots, audit_guard: AuditGuard, repository,
                 runner, query_sets: Dict[str, Any], gateway_client=None,
                 stale_threshold: float = 120.0, schedules=None) -> None:
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
        # None when the schedule table is not there. The screen then says the
        # feature is off rather than offering a form that cannot save.
        self.schedules = schedules

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

        Tolerated rather than raised so a database blip does not replace the
        whole page with an error while the run history still has something
        to say.
        """
        try:
            return list(self.query_sets.values())
        except QuerySetStoreUnavailable as exc:
            log.warning("query set store unavailable: %s", exc)
            return []

    def _not_while_running(self, key: str) -> None:
        """⛔ Refuse to edit a set that is being executed right now.

        The runner reads the statements once, at start. An edit mid-run would
        not change what executes - it would change what the set claims
        executed.
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
        """Production protection, as data. Shown before anyone presses anything."""
        return guards.check(cluster, self.gateway, self.snapshots,
                            self._stale_threshold)

    # ------------------------------------------------------------ reading

    def overview(self, principal: Principal) -> Dict[str, Any]:
        """Every cluster at once, each with its own readiness."""
        self._require_view(principal)
        try:
            recent = self.repository.recent(limit=25)
            active = self.repository.active()
        except BenchmarkStoreUnavailable as exc:
            log.warning("benchmark store unavailable: %s", exc)
            recent, active = [], []

        running = {r["cluster"] for r in active}
        clusters = []
        for cluster in self.config.cluster_names:
            guard = self.guard_for(cluster)
            clusters.append({
                "name": cluster,
                "guard": guard.as_dict(),
                # Two runs on one cluster measure each other. Production
                # traffic is a caveat on the numbers, not a refusal.
                "ready": cluster not in running,
                "exclusive": guard.ok,
                "caveat": guard.caveat(),
                "busy": cluster in running,
            })
        return {
            "clusters": clusters,
            "query_sets": [s.as_dict() for s in self._all_sets()],
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
        # Repetitions folded into per-query medians. Which executions belong
        # together, and what the middle of them is, are decisions about the
        # numbers - so they are made once here rather than in each caller.
        found = dict(found)
        found["by_query"] = query_rows(found)
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
                      limit: int = 100, bucket: str = trend.BY_RUN) -> Dict[str, Any]:
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
            # Aggregated here rather than by the caller: which runs group
            # together and what the middle of a run is are decisions about the
            # numbers, not about how to draw them.
            "trend": trend.build(rows, bucket=bucket),
            # The axis choices, named by the server so a screen cannot offer
            # a grouping the aggregation does not implement.
            "buckets": [{"value": b, "label": trend.BUCKET_LABELS[b]}
                        for b in trend.BUCKETS],
        }

    def _run_snapshot(self, run_id) -> List[Dict[str, Any]]:
        try:
            found = self.repository.get(run_id)
        except BenchmarkStoreUnavailable:
            return []
        return list((found or {}).get("queries") or [])

    # ------------------------------------------------------------ writing

    def start(self, principal: Principal, cluster: str, query_set: str,
              reason: str, repetitions: int = 1, label: Optional[str] = None,
              schedule_id: Any = None) -> Dict[str, Any]:
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

        # ⛔ Recorded, not enforced. A heavy set on a serving cluster competes
        # with production for the same workers and can cause the slowdown it
        # is measuring - so the condition travels with the run instead.
        guard = self.guard_for(cluster)

        try:
            return self._start_audited(principal, cluster, declared, reason,
                                       repetitions, label, guard, schedule_id)
        except ReasonRequired as exc:
            raise ReasonRequiredError(str(exc))
        except AuditUnavailable as exc:
            raise AuditUnavailableError(str(exc))

    def _start_audited(self, principal, cluster, declared, reason, repetitions,
                       label, guard, schedule_id=None):
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
                    queries=[q.as_dict() for q in declared.queries],
                    schedule_id=schedule_id)
            except ActiveRunExists:
                raise BenchmarkAlreadyRunning(
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

    # ------------------------------------------------------- schedules (D-017)

    @contextmanager
    def _schedule_audit(self, principal: Principal, reason, name: str):
        """Reason, audit record, administrator, around one schedule edit.

        ⛔ Its own action type. A schedule authorises *future* unattended runs,
        so "who set this cluster up to be benchmarked every night" has to be
        answerable without reading every run it produced.
        """
        try:
            with self.audit.action(
                actor=principal.username, roles=principal.roles,
                action_type=ACTION_BENCHMARK_SCHEDULE_CHANGE,
                target_kind=TARGET_BENCHMARK_SET, target_id=name,
                reason=reason, actor_ip=principal.ip,
            ):
                yield
        except ReasonRequired as exc:
            raise ReasonRequiredError(str(exc))
        except AuditUnavailable as exc:
            raise AuditUnavailableError(str(exc))

    def _schedules_or_503(self):
        if self.schedules is None:
            raise UpstreamUnavailable(
                "Benchmark schedules are not available - migration 020 has not "
                "been applied, or the schedule store could not be opened.")
        return self.schedules

    def list_schedules(self, principal: Principal) -> Dict[str, Any]:
        self._require_view(principal)
        if self.schedules is None:
            return {"available": False, "schedules": [],
                    "can_edit": principal.can(MANAGE_HEALTH),
                    "min_interval_minutes": scheduling.MIN_INTERVAL_MINUTES,
                    "failure_limit": scheduling.FAILURE_LIMIT}
        try:
            rows = self.schedules.list()
        except scheduling.ScheduleStoreUnavailable as exc:
            raise UpstreamUnavailable(str(exc))
        return {
            "available": True,
            "schedules": [_schedule(row) for row in rows],
            "can_edit": principal.can(MANAGE_HEALTH),
            "min_interval_minutes": scheduling.MIN_INTERVAL_MINUTES,
            "failure_limit": scheduling.FAILURE_LIMIT,
        }

    def create_schedule(self, principal: Principal, name: str, query_set: str,
                        clusters: List[str], interval_minutes: Any,
                        reason: Optional[str], repetitions: Any = 1,
                        label: Optional[str] = None,
                        starts_at: Any = None) -> Dict[str, Any]:
        """⛔ `reason` is not paperwork here. Nobody is present when a scheduled
        run executes, so this is the only explanation the audit record will
        ever carry."""
        self._require_admin(principal)
        store = self._schedules_or_503()
        try:
            fields = scheduling.validate(name, interval_minutes, repetitions,
                                         clusters, reason)
        except ValueError as exc:
            raise InvalidRequest(str(exc))

        self._set_or_404(query_set)
        for cluster in fields["clusters"]:
            self._cluster_or_404(cluster)

        first = _moment(starts_at) or scheduling.utcnow()
        with self._schedule_audit(principal, fields["reason"], fields["name"]):
            try:
                row = store.create(query_set=query_set, label=label,
                                   next_run_at=first,
                                   created_by=principal.username, **fields)
            except ValueError as exc:
                raise InvalidRequest(str(exc))
            except scheduling.ScheduleStoreUnavailable as exc:
                raise UpstreamUnavailable(str(exc))
        return _schedule(row)

    def set_schedule_enabled(self, principal: Principal, schedule_id: Any,
                             enabled: bool, reason: Optional[str]) -> Dict[str, Any]:
        """Switch one on or off by hand.

        Enabling clears the failure count and the reason TMS paused it - the
        operator is saying they have dealt with whatever it was, and a counter
        that survived would trip again three failures later without three more
        failures.
        """
        self._require_admin(principal)
        store = self._schedules_or_503()
        row = store.get(schedule_id)
        if row is None:
            raise NotFound("No such schedule: {}".format(schedule_id))

        changes = {"enabled": bool(enabled)}
        if enabled:
            changes.update(consecutive_failures=0, paused_reason=None,
                           next_run_at=scheduling.advance(
                               _moment(row["next_run_at"]) or scheduling.utcnow(),
                               row["interval_minutes"]))
        with self._schedule_audit(principal, reason, str(row["name"])):
            updated = store.update(schedule_id, **changes)
        return _schedule(updated)

    def delete_schedule(self, principal: Principal, schedule_id: Any,
                        reason: Optional[str]) -> None:
        """The runs it started are untouched - `schedule_id` is ON DELETE SET
        NULL, because the measurements outlive the reason they were taken."""
        self._require_admin(principal)
        store = self._schedules_or_503()
        row = store.get(schedule_id)
        if row is None:
            raise NotFound("No such schedule: {}".format(schedule_id))
        with self._schedule_audit(principal, reason, str(row["name"])):
            store.delete(schedule_id)

    def tick_schedules(self, now=None) -> List[Dict[str, Any]]:
        """Start whatever is due. Called on a timer, by nobody in particular.

        ⛔ Not a request. There is no `principal` because there is no person -
        the schedule's `created_by` is the actor and its `reason` is the why,
        which is exactly what makes an unattended write legal under absolute
        rule 3.

        Returns what it did, for the log. Never raises: this runs in a
        background thread, and a schedule that cannot start must not take the
        thread down with it.
        """
        if self.schedules is None:
            return []
        try:
            due = self.schedules.claim_due(now)
        except Exception:  # noqa: BLE001 - a background tick reports, never dies
            log.exception("could not claim due benchmark schedules")
            return []

        outcomes = []
        for row in due:
            outcomes.append(self._fire(row))
        return outcomes

    def _fire(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """One schedule's turn. Records what happened on the row."""
        actor = Principal(row["created_by"], [ROLE_ADMIN])
        started, refused = [], []
        for cluster in row["clusters"] or []:
            try:
                run = self.start(actor, cluster, query_set=row["query_set"],
                                 reason=row["reason"],
                                 repetitions=row["repetitions"],
                                 label=row.get("label"),
                                 schedule_id=row["id"])
                started.append(run["cluster"])
            except BenchmarkAlreadyRunning as exc:
                # ⛔ Skipped, not failed. The previous run has not finished, so
                # this is the guard working - counting it as a failure would
                # pause the schedule for doing the right thing.
                refused.append({"cluster": cluster, "message": str(exc),
                                "skipped": True})
            except Exception as exc:  # noqa: BLE001
                log.warning("schedule %s could not start on %s: %s",
                            row["name"], cluster, exc)
                refused.append({"cluster": cluster, "message": str(exc),
                                "skipped": False})

        broke = [r for r in refused if not r["skipped"]]
        changes: Dict[str, Any] = {"last_run_at": scheduling.utcnow()}
        if started:
            changes.update(last_outcome="started", consecutive_failures=0)
        elif broke:
            failures = int(row.get("consecutive_failures") or 0) + 1
            changes.update(last_outcome=broke[0]["message"][:500],
                           consecutive_failures=failures)
            if failures >= scheduling.FAILURE_LIMIT:
                # A broken set running every night forever is load with no
                # reader. Switched off *for* the operator, with the reason on
                # the row so the screen can say which it was.
                changes.update(enabled=False, paused_reason=(
                    "Paused after {} failures in a row. Last: {}".format(
                        failures, broke[0]["message"][:300])))
                log.error("benchmark schedule %s paused after %d failures",
                          row["name"], failures)
        else:
            changes.update(last_outcome="skipped - a run was already going")

        try:
            self.schedules.update(row["id"], **changes)
        except Exception:  # noqa: BLE001
            log.exception("could not record the outcome of schedule %s", row["name"])
        return {"schedule": row["name"], "started": started, "refused": refused}

    def start_many(self, principal: Principal, clusters: List[str], query_set: str,
                   reason: str, repetitions: int = 1,
                   label: Optional[str] = None) -> Dict[str, Any]:
        """Start the same set on several clusters. Report each outcome.

        ⛔ One refusal does not cancel the others. Cancelling everything would
        mean re-running the rest later, on clusters whose caches are now warm -
        and those are the numbers that would get compared.
        """
        self._require_admin(principal)
        if not clusters:
            raise InvalidRequest("Select at least one cluster.")
        for cluster in clusters:
            self._cluster_or_404(cluster)

        started, refused = [], []
        for cluster in clusters:
            try:
                started.append(self.start(principal, cluster, query_set=query_set,
                                          reason=reason, repetitions=repetitions,
                                          label=label))
            except (ReasonRequiredError, Forbidden, NotFound):
                # Nothing cluster-specific about these - the request itself is
                # wrong, so failing the whole thing is the honest answer.
                raise
            except ApiError as exc:
                refused.append({"cluster": cluster, "message": exc.message})
        if not started and refused:
            # Every one was refused. Raising rather than reporting keeps the
            # operator on the form with their reason still in it.
            raise InvalidRequest("; ".join(
                "{}: {}".format(r["cluster"], r["message"]) for r in refused))
        return {"started": started, "refused": refused}

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


    # -------------------------------------------------------- editing

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

    def create_set(self, principal: Principal, key: str, title: str,
                   description: str, name: str, statement: str,
                   reason: str) -> Dict[str, Any]:
        """A set and its first query, in one step."""
        self._require_admin(principal)
        refusal = refuse_name(key, "set key")
        if refusal:
            raise InvalidRequest(refusal)
        refusal = refuse_name(name, "query name")
        if refusal:
            raise InvalidRequest(refusal)
        statement = (statement or "").strip().rstrip(";")
        refusal = refuse_statement(statement)
        if refusal:
            raise InvalidRequest("That statement cannot be benchmarked: {}."
                                 .format(refusal))
        if self.query_sets.get(key) is not None:
            raise InvalidRequest(
                "A query set named {!r} already exists.".format(key))

        # Validated first, written second, so a rejected statement never
        # leaves an empty set behind for somebody to wonder about.
        self.save_set(principal, key, title, description, reason)
        self.save_query(principal, key, name=name, title="", statement=statement,
                        reason=reason)
        return self.query_sets.get(key).as_dict()

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
        """Reason, audit record, administrator, around one edit.

        The previous statement is not copied into the audit row:
        `benchmark_run.queries` already holds every version that was executed.
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


def _moment(value: Any) -> Optional[datetime]:
    """A timestamp from a request body, or None."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _schedule(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """One schedule, as the console reads it.

    `paused_reason` is separate from `enabled` on purpose: "somebody switched
    this off" and "this broke and was switched off for them" are different
    answers, and only the second one needs acting on.
    """
    if row is None:
        return {}
    out = dict(row)
    for column in ("next_run_at", "last_run_at", "created_at"):
        value = out.get(column)
        out[column] = value.isoformat() if hasattr(value, "isoformat") else value
    out["clusters"] = list(out.get("clusters") or [])
    out["paused_by_tms"] = bool(out.get("paused_reason")) and not out.get("enabled")
    return out
