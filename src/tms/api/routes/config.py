"""Cluster configuration: what is on each node, and changing it.

Reads (D-018 §1) and edits (§3). The edits go through `config_edit`, which is
off unless a deploy playbook is configured - and it is a *fourth* playbook,
separate from restart, scan and catalog deploy.

⛔ Nothing here restarts anything. Trino reads config.properties at startup, so
a deploy leaves a changed file on a cluster still running the old values. The
restart is the safe sequence's job and it drains first.

Python 3.9 compatible.
"""

from typing import Any, Dict

from tms.api.routes.deps import Deps


def register(app, deps: Deps) -> None:
    from fastapi import Body, Depends

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

    # -------------------------------------------------- editing (D-018 §3)

    def editor():
        return deps.require("config_edit")

    @app.get("/api/v1/clusters/{cluster}/config/changes")
    def list_changes(cluster: str, principal: Principal = Depends(principal_of)):
        """Every change set, with the verdict for each cluster attached.

        ⛔ The verdicts come from here, not from the browser. Whether a change
        may go somewhere depends on the scan, the development list and the
        proof mark, and a second copy of that logic would drift from the one
        the deploy endpoint applies.
        """
        return editor().overview(principal, cluster)

    @app.post("/api/v1/config/changes", status_code=201)
    def create_change(body: Dict[str, Any] = Body(...),
                      principal: Principal = Depends(principal_of)):
        return editor().create(principal, title=body.get("title"),
                               target_role=body.get("target_role"),
                               entries=body.get("entries") or [],
                               notes=body.get("notes"),
                               reason=body.get("reason"))

    @app.post("/api/v1/config/changes/{change_id}")
    def update_change(change_id: str, body: Dict[str, Any] = Body(...),
                      principal: Principal = Depends(principal_of)):
        """Edit a change. ⛔ This clears the development-cluster proof."""
        return editor().update(principal, change_id, title=body.get("title"),
                               target_role=body.get("target_role"),
                               entries=body.get("entries") or [],
                               notes=body.get("notes"),
                               reason=body.get("reason"))

    @app.post("/api/v1/config/changes/{change_id}/delete")
    def delete_change(change_id: str, body: Dict[str, Any] = Body(...),
                      principal: Principal = Depends(principal_of)):
        return editor().delete(principal, change_id, reason=body.get("reason"))

    @app.post("/api/v1/clusters/{cluster}/config/changes/{change_id}/deploy",
              status_code=202)
    def deploy_change(cluster: str, change_id: str,
                      body: Dict[str, Any] = Body(...),
                      principal: Principal = Depends(principal_of)):
        """Write the change onto this cluster's nodes.

        202: the playbook runs against every targeted node and takes longer
        than a request should. The screen polls.

        ⛔ Nothing is restarted. The cluster keeps running the old values until
        the safe sequence restarts it.
        """
        return editor().deploy(principal, change_id, cluster,
                               reason=body.get("reason"))
