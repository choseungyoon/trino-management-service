"""Fleet: node inventory, graceful shutdown, and configured playbooks.

⛔ Shutting a worker down is destructive and irreversible from here - Trino
drains it and it does not come back on its own. Reason, audit, administrator,
and the service refuses without them.

⛔ A job is not a restart. Nothing here checks that a cluster was drained, so
a playbook that restarts something would skip the sequence that does. The
config check refuses that case at startup.

Python 3.9 compatible.
"""

from typing import Any, Dict

from tms.api.routes.deps import Deps


def register(app, deps: Deps) -> None:
    from fastapi import Body, Depends

    from tms.api.permissions import Principal

    principal_of = deps.current_principal

    def fleet():
        return deps.require("fleet")

    @app.get("/api/v1/clusters/{cluster}/fleet")
    def get_fleet(cluster: str, principal: Principal = Depends(principal_of)):
        """Nodes as the coordinator and the inventory each see them.

        The two counts are reported separately on purpose. TMS cannot list
        node identities - `system.runtime.nodes` needs a permission it
        deliberately does not hold - so a disagreement is reported as a
        disagreement rather than resolved into a guess.
        """
        return fleet().get_fleet(principal, cluster)

    @app.post("/api/v1/clusters/{cluster}/fleet/identify")
    def identify(cluster: str, principal: Principal = Depends(principal_of)):
        """Which inventory hosts the coordinator does not see.

        Costs the coordinator a query, so it is asked for rather than polled.
        """
        return fleet().identify_unjoined(principal, cluster)

    @app.post("/api/v1/clusters/{cluster}/fleet/nodes/{host}/shutdown")
    def shutdown(cluster: str, host: str, body: Dict[str, Any] = Body(...),
                 principal: Principal = Depends(principal_of)):
        return fleet().shutdown_node(principal, cluster, host,
                                     reason=body.get("reason"))

    @app.get("/api/v1/clusters/{cluster}/fleet/jobs")
    def list_jobs(cluster: str, principal: Principal = Depends(principal_of)):
        return fleet().list_jobs(principal, cluster)

    @app.get("/api/v1/fleet/jobs/{run_id}")
    def get_job(run_id: str, principal: Principal = Depends(principal_of)):
        """One run with its output.

        The log is verbatim text from the playbook - rendered as a terminal,
        never as something TMS is asserting.
        """
        return fleet().get_job_run(principal, run_id)

    @app.post("/api/v1/clusters/{cluster}/fleet/jobs/{key}", status_code=201)
    def start_job(cluster: str, key: str, body: Dict[str, Any] = Body(...),
                  principal: Principal = Depends(principal_of)):
        return fleet().start_job(principal, cluster, key,
                                 parameters=body.get("parameters") or {},
                                 reason=body.get("reason"))
