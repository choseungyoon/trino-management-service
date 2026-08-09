"""The safe restart sequence (FR-CO-02).

CLAUDE.md rule 5 fixes the order and forbids any path that skips it:

    deactivate -> confirm intake stopped -> drain -> restart
      -> confirm health -> reactivate

Restarting a coordinator kills every query running on it, so the whole point is
that the restart cannot happen until the cluster is empty, and traffic cannot
return until the cluster is healthy again.

Why the restart itself is not performed here
--------------------------------------------
Step 4 is a seam, not an implementation (`ops/executor.py`). This module knows
only that the cluster is being restarted and what has to be true before and
after; whether a human does it or `ansible-playbook` does is decided by
configuration, and neither the state machine, the audit trail nor the screen
changes between them.

That is why the manual mode is not a lesser version of the feature. The gate is
what prevents the incident - a cluster that is empty before the restart and
healthy before traffic returns - and the gate is identical either way.

State machine
-------------
    PENDING ─deactivate→ DRAINING ─(running==0)→ DRAINED
    DRAINED ─restart→ RESTARTING ─(restart done + health GOOD)→ VERIFYING
    VERIFYING ─reactivate→ COMPLETED

    any active state ─abort→ ABORTING ─reactivate→ ABORTED

⛔ Aborting always reactivates. A sequence abandoned half way leaves a cluster
   receiving no traffic, which is itself an outage - and a quiet one, because
   everything looks healthy. Abort is not "stop doing things", it is "put it
   back".

Python 3.9 compatible.
"""

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

# ------------------------------------------------------------------- states

PENDING = "PENDING"
DRAINING = "DRAINING"
DRAINED = "DRAINED"
RESTARTING = "RESTARTING"
VERIFYING = "VERIFYING"
COMPLETED = "COMPLETED"
ABORTING = "ABORTING"
ABORTED = "ABORTED"

TERMINAL = (COMPLETED, ABORTED)
# States in which the cluster is deactivated and therefore taking no traffic.
TRAFFIC_STOPPED = (DRAINING, DRAINED, RESTARTING, VERIFYING, ABORTING)

STEP_ORDER = (PENDING, DRAINING, DRAINED, RESTARTING, VERIFYING, COMPLETED)

# Log levels. `output` is verbatim text from whatever performed the restart -
# rendered as a terminal rather than as prose, and never mistaken for something
# TMS is asserting.
#
# ⛔ Mirrored by the CHECK constraint on restart_sequence_event.level. Adding a
# level here without a migration makes every save fail once it is first used,
# so `tests/test_safe_sequence.py` compares the two.
LEVEL_INFO = "info"
LEVEL_WARN = "warn"
LEVEL_ERROR = "error"
LEVEL_OUTPUT = "output"
ALLOWED_LEVELS = (LEVEL_INFO, LEVEL_WARN, LEVEL_ERROR, LEVEL_OUTPUT)

# Two vocabularies, because they answer different questions.
#
# STATE_LABELS answers "what is happening right now" - used in the banner that
# follows the operator around, and in history rows.
STATE_LABELS = {
    PENDING: "Not started",
    DRAINING: "Draining — waiting for running queries to finish",
    DRAINED: "Drained — safe to restart",
    RESTARTING: "Restart in progress",
    VERIFYING: "Verifying health before restoring traffic",
    COMPLETED: "Completed — traffic restored",
    ABORTING: "Aborting — restoring traffic",
    ABORTED: "Aborted — traffic restored",
}

# CHECKLIST_LABELS answers "what does this step do" - the six lines beside the
# ticks. Each names the action taken to *leave* that state, which is what a
# tick against it should mean.
#
# Deliberately silent about who performs the restart: with an automated
# executor TMS does it, with a manual one the operator does, and a checklist
# that hard-codes either is wrong half the time. The action panel says who.
CHECKLIST_LABELS = {
    PENDING: "Stop new queries reaching it, in the Gateway",
    DRAINING: "Wait for every running query to finish",
    DRAINED: "Confirm the cluster is empty",
    RESTARTING: "Restart it",
    VERIFYING: "Verify health is GOOD",
    COMPLETED: "Put it back in rotation",
}

# Kept as an alias: `label` in the API payload has always meant the state.
STEP_LABELS = STATE_LABELS


def checklist():
    """The six steps, for showing the procedure before it starts.

    Same source as the live checklist. Two hand-written copies would drift, and
    the one place that must not lie about the order is the screen you read
    before deciding to take a cluster out of rotation.
    """
    return [{"state": state, "label": CHECKLIST_LABELS[state], "number": index + 1}
            for index, state in enumerate(STEP_ORDER)]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SequenceError(Exception):
    """A transition that the sequence does not allow."""


class StepBlocked(SequenceError):
    """The transition is legal but its precondition is not met yet.

    Separate from a plain SequenceError because this is a normal state - the
    operator is early, not wrong - and the UI phrases it differently.
    """


class RestartSequence:
    """One restart of one cluster.

    Deliberately holds no clients and does no I/O: callers feed it observations
    (running query count, health state) and it decides what may happen next.
    That keeps every ordering rule testable without a Trino or a Gateway.
    """

    __slots__ = ("cluster", "reason", "actor", "state", "history",
                 "drain_timeout_seconds", "running_queries", "health_state",
                 "_clock")

    def __init__(self, cluster: str, reason: str, actor: str,
                 drain_timeout_seconds: float = 900.0,
                 clock: Optional[Callable[[], datetime]] = None) -> None:
        if not (reason or "").strip():
            raise SequenceError("a reason is required to restart a cluster")
        self.cluster = cluster
        self.reason = reason.strip()
        self.actor = actor
        self.state = PENDING
        self.history: List[Dict[str, Any]] = []
        self.drain_timeout_seconds = drain_timeout_seconds
        self.running_queries: Optional[int] = None
        self.health_state: Optional[str] = None
        self._clock = clock or _now

    # ------------------------------------------------------------- helpers

    def log(self, message: str, level: str = "info") -> None:
        """Append a line to the running progress log.

        The log is the screen. An operator watching a cluster get restarted
        wants to see it happening, in the order it happens, in the words they
        would use themselves - not a status field that silently changes value.
        """
        if level not in ALLOWED_LEVELS:
            # Caught here rather than by the database, which would fail the
            # whole save and lose the line that was being recorded.
            raise SequenceError("unknown log level: {!r}".format(level))
        self.history.append({
            "at": self._clock().isoformat(),
            "state": self.state,
            "level": level,
            "message": message,
        })

    def _record(self, step: str, note: str = "") -> None:
        self.state = step
        if note:
            self.log(note)

    def _require(self, *allowed: str) -> None:
        if self.state not in allowed:
            raise SequenceError(
                "cannot do that from {} (expected {})".format(
                    self.state, " or ".join(allowed)))

    @property
    def traffic_stopped(self) -> bool:
        return self.state in TRAFFIC_STOPPED

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL

    @property
    def needs_operator_action(self) -> bool:
        """True exactly when TMS is waiting on a human.

        Used by the UI to make the hand-off obvious instead of leaving someone
        staring at a screen that appears to be working on something.
        """
        return self.state in (DRAINED, RESTARTING)

    # ----------------------------------------------------------- transitions

    def begin(self) -> None:
        """Step 1: the cluster has been deactivated in the Gateway."""
        self._require(PENDING)
        self._record(DRAINING,
                     "Blocking new queries to {} in the Gateway.".format(self.cluster))

    def observe(self, running_queries: int, health_state: Optional[str] = None) -> None:
        """Feed in what the collector currently sees."""
        previous = self.running_queries
        self.running_queries = running_queries
        if health_state is not None:
            self.health_state = health_state
        if self.state == DRAINING and running_queries == 0:
            self._record(DRAINED, "All running queries have finished. "
                                  "{} is empty.".format(self.cluster))
        elif self.state == DRAINING and running_queries > 0 and running_queries != previous:
            # Progress, not a state change - the operator wants to watch the
            # queue drain rather than stare at an unchanging screen.
            #
            # Only on change: the live view re-observes every couple of seconds,
            # and repeating "waiting for 3 queries" thirty times a minute buries
            # the lines that mean something in a log that is also the record of
            # what was done to production.
            self.log("Waiting for {} running quer{} to finish.".format(
                running_queries, "y" if running_queries == 1 else "ies"))

    def confirm_drained(self) -> None:
        """Step 2/3: intake stopped and the cluster is empty."""
        self._require(DRAINING, DRAINED)
        if self.running_queries is None:
            raise StepBlocked(
                "no observation yet - TMS has not seen this cluster's query count")
        if self.running_queries > 0:
            raise StepBlocked(
                "{} quer{} still running. Restarting now would kill {}.".format(
                    self.running_queries,
                    "y is" if self.running_queries == 1 else "ies are",
                    "it" if self.running_queries == 1 else "them"))
        self._record(DRAINED, "Confirmed: no queries are running on {}.".format(
            self.cluster))

    def force_drained(self, override_reason: str) -> None:
        """Proceed with queries still running.

        Exists because a query can hang forever and someone eventually has to
        decide. It is a separate, explicitly-named action rather than a flag on
        the normal path, it demands its own reason, and it records exactly how
        many queries were about to be killed - so the decision is legible
        afterwards instead of looking like a routine drain.
        """
        self._require(DRAINING, DRAINED)
        if not (override_reason or "").strip():
            raise SequenceError("forcing past a non-empty cluster requires its own reason")
        killed = self.running_queries or 0
        self._record(DRAINED, "FORCED past the drain with {} quer{} still "
                              "running. They will be killed by the restart. "
                              "Reason: {}".format(
                                  killed, "y" if killed == 1 else "ies",
                                  override_reason.strip()))
        self.history[-1]["level"] = "warn"

    def mark_restarting(self) -> None:
        """Step 4 begins. The operator restarts the cluster themselves."""
        self._require(DRAINED)
        self._record(RESTARTING, "Bringing {} down and back up.".format(self.cluster))

    def mark_restarted(self) -> None:
        self._require(RESTARTING)
        self._record(VERIFYING, "{} restarted. Checking health before restoring "
                                "traffic.".format(self.cluster))

    def confirm_healthy(self) -> None:
        """Step 5: health must be good before traffic returns."""
        self._require(VERIFYING)
        if self.health_state != "GOOD":
            raise StepBlocked(
                "health is {} - traffic is not restored until it is GOOD".format(
                    self.health_state or "unknown"))
        self.log("Health is GOOD.")

    def complete(self) -> None:
        """Step 6: the cluster has been reactivated in the Gateway."""
        self._require(VERIFYING)
        if self.health_state != "GOOD":
            raise StepBlocked(
                "refusing to restore traffic while health is {}".format(
                    self.health_state or "unknown"))
        self._record(COMPLETED,
                     "{} is back in rotation. Traffic restored.".format(self.cluster))

    def begin_abort(self, note: str = "") -> None:
        if self.is_terminal:
            raise SequenceError("this sequence has already finished")
        self._record(ABORTING, note or
                     "Aborting. Restoring traffic to {} before stopping.".format(
                         self.cluster))
        self.history[-1]["level"] = "warn"

    def finish_abort(self) -> None:
        """Only reached once the cluster is active again."""
        self._require(ABORTING)
        self._record(ABORTED, "Aborted. {} is back in rotation.".format(self.cluster))

    # ---------------------------------------------------------------- view

    def steps(self) -> List[Tuple[str, str, str]]:
        """(state, label, status) for rendering the sequence as a checklist.

        `current` is what the UI animates, so exactly one step may hold it and
        a finished sequence may hold none. COMPLETED is the last step having
        *happened*, not a step still in progress - leaving it "current" left a
        pulsing dot on a restart that was over.
        """
        current = STEP_ORDER.index(self.state) if self.state in STEP_ORDER else None
        rows = []
        for index, step in enumerate(STEP_ORDER):
            if self.state in (ABORTING, ABORTED):
                status = "aborted"
            elif self.state == COMPLETED:
                status = "done"
            elif current is None:
                status = "pending"
            elif index < current:
                status = "done"
            elif index == current:
                status = "current"
            else:
                status = "pending"
            rows.append((step, CHECKLIST_LABELS[step], status))
        return rows

    def as_dict(self) -> Dict[str, Any]:
        return {
            "cluster": self.cluster,
            "reason": self.reason,
            "actor": self.actor,
            "state": self.state,
            "label": STATE_LABELS.get(self.state, self.state),
            "running_queries": self.running_queries,
            "health_state": self.health_state,
            "traffic_stopped": self.traffic_stopped,
            "needs_operator_action": self.needs_operator_action,
            "is_terminal": self.is_terminal,
            "history": list(self.history),
        }
