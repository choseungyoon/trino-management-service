"""Resource groups: the tree Trino reads to decide whether to admit a query.

⛔ Every rule stays on the server. Validation here is not a convenience the
client may skip - a bad value reaches every coordinator within the refresh
interval, and there is no restart acting as a gate. The service refuses; these
routes only carry the refusal.

Reads are available to viewers, writes to administrators, and every write
needs a reason.

Python 3.9 compatible.
"""

from typing import Any, Dict, Optional

from tms.api.routes.deps import Deps


def register(app, deps: Deps) -> None:
    from fastapi import Body, Depends, Query

    from tms.api.permissions import Principal

    principal_of = deps.current_principal
    service = deps.service

    @app.get("/api/v1/clusters/{cluster}/resource-groups")
    def get_tree(cluster: str, principal: Principal = Depends(principal_of)):
        """The configured tree, with running state where it is known.

        No freshness envelope: this is read from the store Trino itself reads,
        so it is current by construction. The one part that can be stale is
        the running-query column, and the payload marks that separately.
        """
        return service.get_resource_group_config(principal, cluster)

    @app.get("/api/v1/clusters/{cluster}/resource-groups/revisions")
    def revisions(cluster: str, limit: int = Query(50, ge=1, le=500),
                  principal: Principal = Depends(principal_of)):
        return {"revisions": service.resource_group_revisions(
            principal, cluster, limit=limit)}

    @app.get("/api/v1/clusters/{cluster}/resource-groups/{row_id}/impact")
    def deletion_impact(cluster: str, row_id: str,
                        principal: Principal = Depends(principal_of)):
        """What deleting this group would take with it.

        Read-only, so a viewer may ask. Shown before the delete rather than
        discovered after it.
        """
        return service.resource_group_deletion_impact(principal, cluster, row_id)

    @app.post("/api/v1/clusters/{cluster}/resource-groups", status_code=201)
    def create_group(cluster: str, body: Dict[str, Any] = Body(...),
                     principal: Principal = Depends(principal_of)):
        return service.create_resource_group(
            principal, cluster, name=str(body.get("name") or ""),
            parent_row_id=body.get("parent_row_id"),
            values=body.get("values") or {}, reason=body.get("reason"))

    @app.patch("/api/v1/clusters/{cluster}/resource-groups/{row_id}")
    def update_group(cluster: str, row_id: str, body: Dict[str, Any] = Body(...),
                     principal: Principal = Depends(principal_of)):
        """Changed fields only.

        PATCH rather than PUT: a full-document write would let a client that
        read the tree a minute ago silently undo somebody else's edit.
        """
        return service.update_resource_group(
            principal, cluster, row_id, changes=body.get("changes") or {},
            reason=body.get("reason"))

    @app.delete("/api/v1/clusters/{cluster}/resource-groups/{row_id}")
    def delete_group(cluster: str, row_id: str, reason: Optional[str] = Query(None),
                     principal: Principal = Depends(principal_of)):
        return service.delete_resource_group(principal, cluster, row_id, reason=reason)

    @app.post("/api/v1/clusters/{cluster}/resource-groups/selectors", status_code=201)
    def create_selector(cluster: str, body: Dict[str, Any] = Body(...),
                        principal: Principal = Depends(principal_of)):
        return service.create_resource_group_selector(
            principal, cluster, target_row_id=body.get("target_row_id"),
            priority=body.get("priority"), matchers=body.get("matchers") or {},
            reason=body.get("reason"))

    @app.delete("/api/v1/clusters/{cluster}/resource-groups/selectors/{selector_id}")
    def delete_selector(cluster: str, selector_id: str,
                        reason: Optional[str] = Query(None),
                        principal: Principal = Depends(principal_of)):
        return service.delete_resource_group_selector(
            principal, cluster, selector_id, reason=reason)

    @app.post("/api/v1/clusters/{cluster}/resource-groups/revisions/{revision_id}/revert")
    def revert(cluster: str, revision_id: str, body: Dict[str, Any] = Body(...),
               principal: Principal = Depends(principal_of)):
        """Undo one revision.

        Its own action type in the audit log rather than a flag on a change,
        so "how often are these undone" stays an answerable question.
        """
        return service.revert_resource_group(
            principal, cluster, revision_id, reason=body.get("reason"))
