"""How the restart step actually happens (FR-CO-02 step 4).

The sequence in `sequence.py` decides *when* a restart may happen. This decides
*how*, and it is deliberately a separate seam because the two have very
different risk profiles and very different reasons to change.

Two implementations exist:

* `ManualExecutor` — TMS stops and asks the operator to restart the cluster
  themselves. No new credentials, no new infrastructure, no blast radius. This
  is the default and it is a complete, safe implementation of the step.
* `AnsibleRestartExecutor` (`ops/ansible.py`) — TMS runs the platform team's
  existing playbook itself.

⛔ The choice between them is a security decision, not a convenience one
--------------------------------------------------------------------
Shelling out to `ansible-playbook` needs SSH access to every Trino node, which
turns TMS from "reads Trino and kills queries" into "can do anything, anywhere,
as root". Every future TMS vulnerability inherits that. A job-runner API
(AWX/AAP) would instead hold a token scoped to specific job templates - the
same operator experience with a far smaller blast radius.

The platform team was shown that trade-off and chose direct execution
(2026-08-08, DECISIONS.md D-008). So the decision is recorded, `manual` remains
the default, and `ops/ansible.py` is written to keep that SSH reach usable for
exactly one configured playbook and nothing else.

Python 3.9 compatible.
"""

import logging
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

# Outcome of asking an executor to restart something.
PENDING_OPERATOR = "pending_operator"   # a human has to act
RUNNING = "running"                     # the executor is working on it
SUCCEEDED = "succeeded"
FAILED = "failed"
# Neither success nor failure: TMS was restarted while the work was in flight
# and can no longer see it. Kept in the shared vocabulary because callers have
# to handle it, and treating it as either outcome is how traffic gets restored
# to a cluster nobody confirmed came back.
UNKNOWN = "unknown"


class RestartExecutor:
    """Interface. Implementations must be safe to call twice.

    The sequence can be resumed after a TMS restart, so an executor may be
    asked about work it already started. `status()` is the source of truth;
    `start()` must not launch a second restart if one is already under way.
    """

    #: Shown in the UI so an operator knows whether to expect TMS to act.
    automated = False
    name = "unknown"

    def start(self, cluster: str, sequence_id: str) -> str:
        raise NotImplementedError

    def status(self, cluster: str, sequence_id: str) -> str:
        raise NotImplementedError

    def describe(self, cluster: str) -> Dict[str, Any]:
        """What the UI should tell the operator to do, if anything."""
        return {}


class ManualExecutor(RestartExecutor):
    """The operator restarts the cluster; TMS holds the gate.

    TMS still guarantees the part that prevents the incident: the cluster is
    empty before this point, and traffic does not return until health is GOOD.
    What it does not do is press the button.
    """

    automated = False
    name = "manual"

    def __init__(self, instructions: Optional[str] = None) -> None:
        self.instructions = instructions or (
            "TMS has stopped new queries and confirmed the cluster is empty. It "
            "will not restart anything — you do that with your normal procedure, "
            "then tell TMS below. It verifies health before restoring traffic."
        )
        self._reported: Dict[str, bool] = {}

    def start(self, cluster: str, sequence_id: str) -> str:
        # Nothing to launch - this exists so the sequence has a uniform shape.
        return PENDING_OPERATOR

    def report_done(self, sequence_id: str) -> None:
        self._reported[sequence_id] = True

    def status(self, cluster: str, sequence_id: str) -> str:
        return SUCCEEDED if self._reported.get(sequence_id) else PENDING_OPERATOR

    def describe(self, cluster: str) -> Dict[str, Any]:
        return {
            "automated": False,
            # ⛔ First person on purpose. "Restart X now" reads as an
            # instruction to TMS, and an operator who presses it then watches
            # nothing happen concludes the feature is broken - which is exactly
            # what happened the first time this was used.
            "title": "I will restart {} myself".format(cluster),
            "instructions": self.instructions,
            # Shown once the sequence is in RESTARTING. The old screen showed
            # only a "it is back up" button here, so at the one moment the
            # operator had to act, nothing on screen said so.
            "waiting": (
                "Restart {} now, using your normal procedure. TMS is holding "
                "the gate and waiting for you - it is not doing anything in the "
                "background. Traffic stays stopped until you confirm below and "
                "health comes back GOOD.".format(cluster)
            ),
        }


def build_executor(config) -> RestartExecutor:
    """Pick an executor from configuration.

    The sequence, the audit trail and the UI are identical either way: none of
    them know how the restart happens, which is what made adding the automated
    transport a matter of writing one class rather than reworking the feature.

    ⛔ Falls back to manual on any construction failure. A misconfigured
    automated restart must not become "TMS cannot restart anything" during an
    incident - the operator can still drive the sequence by hand, which is the
    part that actually prevents the outage.
    """
    ops = getattr(config, "cluster_ops", None)
    mode = getattr(ops, "restart_mode", "manual")
    if mode != "ansible":
        return ManualExecutor()

    from tms.ops.ansible import AnsibleError, AnsibleRestartExecutor

    settings = ops.ansible
    try:
        return AnsibleRestartExecutor(
            playbook=settings.playbook,
            cluster_inventories=settings.inventories,
            binary=settings.binary,
            timeout_seconds=settings.timeout_seconds,
            extra_vars=settings.extra_vars,
            state_dir=settings.state_dir,
        )
    except AnsibleError as exc:
        log.error(
            "cluster_ops.restart_mode is 'ansible' but the executor could not be "
            "built (%s). Falling back to manual restarts - the sequence still "
            "works, TMS just will not press the button.", exc)
        return ManualExecutor()
