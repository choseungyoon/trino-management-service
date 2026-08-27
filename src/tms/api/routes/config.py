"""Cluster configuration as it actually is on each node (FR-CO-01, D-018 §1).

Two endpoints and no writes. Deploying is a later step, and it does not exist
yet - which is a property an operator can confirm by reading this file.

Python 3.9 compatible.
"""

from tms.api.routes.deps import Deps


def register(app, deps: Deps) -> None:
    from fastapi import Depends

    from tms.api.permissions import Principal

    principal_of = deps.current_principal

    def scanner():
        return deps.require("config_scan")

    @app.get("/api/v1/clusters/{cluster}/config")
    def get_config(cluster: str, principal: Principal = Depends(principal_of)):
        """The last scan, with the disagreements already worked out.

        ⛔ Reading never scans. A fleet-wide SSH fan-out on a page load would
        make opening the screen an act with consequences.
        """
        return scanner().get(principal, cluster)

    @app.post("/api/v1/clusters/{cluster}/config/scan", status_code=202)
    def scan(cluster: str, principal: Principal = Depends(principal_of)):
        """Ask every node what it has. Administrators only.

        202 and returns immediately: this connects to every node in the
        cluster and takes longer than a request should. The screen polls.
        """
        return scanner().scan(principal, cluster)
