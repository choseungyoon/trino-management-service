"""How the restart step actually happens (FR-CO-02 step 4).

The sequence in `sequence.py` decides *when* a restart may happen. This decides
*how*, and it is deliberately a separate seam because the two have very
different risk profiles and very different reasons to change.

Two implementations are expected:

* `ManualExecutor` — TMS stops and asks the operator to restart the cluster
  themselves. No new credentials, no new infrastructure, no blast radius. This
  is the default and it is a complete, safe implementation of the step.
* An Ansible-backed executor — TMS triggers the existing playbook. The
  platform team already runs config/catalog deployment and coordinator/worker
  restarts this way, so the capability exists; what is left is deciding how TMS
  reaches it.

⛔ The choice between them is a security decision, not a convenience one
--------------------------------------------------------------------
If TMS shells out to `ansible-playbook` it needs SSH access to every Trino
node, which turns TMS from "reads Trino and kills queries" into "can do
anything, anywhere, as root". Every future TMS vulnerability inherits that.

If instead TMS calls a job-runner API (AWX/AAP or similar) it holds a token
scoped to specific job templates. It can restart a coordinator and nothing
else. Same operator experience, far smaller blast radius.

That is why `AnsibleExecutor` is not written yet: picking the transport by
guessing would make the security decision as a side effect of an
implementation detail.

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
            "Restart the coordinator using your normal procedure, then mark it "
            "restarted below. TMS has already stopped new queries and confirmed "
            "the cluster is empty; it will verify health before restoring traffic."
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
            "title": "Restart {} now".format(cluster),
            "instructions": self.instructions,
        }


def build_executor(config) -> RestartExecutor:
    """Pick an executor from configuration.

    Only the manual one exists today. When an automated transport is chosen,
    it is added here - the sequence, the audit trail and the UI do not change,
    because none of them know how the restart happens.
    """
    mode = getattr(getattr(config, "cluster_ops", None), "restart_mode", "manual")
    if mode != "manual":
        log.warning(
            "cluster_ops.restart_mode=%r is not implemented; falling back to "
            "manual. Automating this step is a security decision - see "
            "tms/ops/executor.py.", mode)
    return ManualExecutor()
