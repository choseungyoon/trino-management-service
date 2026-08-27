"""Catalogs: drafts in TMS, files on the nodes (FR-CATALOG, D-018 §2).

⛔ Deploying writes a file and stops. Restarting is a separate act with its own
sequence, and there is no endpoint here that does both - that would be the path
around the drain (absolute rule 5).

Python 3.9 compatible.
"""

from typing import Any, Dict, Optional

from tms.api.routes.deps import Deps


def register(app, deps: Deps) -> None:
    from fastapi import Body, Depends, Query

    from tms.api.permissions import Principal

    principal_of = deps.current_principal

    def catalogs():
        return deps.require("catalogs")

    @app.get("/api/v1/catalogs")
    def overview(principal: Principal = Depends(principal_of)):
        """Every draft, where each may go, and what has been deployed.

        `targets` carries the refusal per cluster rather than a boolean: a
        greyed button that cannot say why is a button people file tickets
        about.
        """
        return catalogs().overview(principal)

    @app.post("/api/v1/catalogs", status_code=201)
    def create(body: Dict[str, Any] = Body(...),
               principal: Principal = Depends(principal_of)):
        """⛔ A credential-shaped property must be `${ENV:VAR}`, not a value.
        Trino resolves it from the node's own environment, so TMS never holds
        the secret."""
        return catalogs().create(
            principal,
            name=str(body.get("name") or ""),
            connector=str(body.get("connector") or ""),
            properties=body.get("properties") or {},
            notes=body.get("notes"),
            reason=body.get("reason"))

    @app.put("/api/v1/catalogs/{catalog_id}")
    def save(catalog_id: int, body: Dict[str, Any] = Body(...),
             principal: Principal = Depends(principal_of)):
        """⛔ The name never changes - it is the filename on every node that
        already has this catalog. Editing anything else clears the development
        proof: an edited draft is a different draft."""
        return catalogs().save(
            principal, catalog_id,
            connector=str(body.get("connector") or ""),
            properties=body.get("properties") or {},
            notes=body.get("notes"),
            reason=body.get("reason"))

    @app.delete("/api/v1/catalogs/{catalog_id}", status_code=204)
    def delete(catalog_id: int, reason: Optional[str] = Query(None),
               principal: Principal = Depends(principal_of)):
        """Deletes the draft, not the files. A catalog already on a cluster
        stays there until it is removed from that cluster."""
        catalogs().delete(principal, catalog_id, reason=reason)
        return None

    @app.post("/api/v1/catalogs/{catalog_id}/deploy", status_code=202)
    def deploy(catalog_id: int, body: Dict[str, Any] = Body(...),
               principal: Principal = Depends(principal_of)):
        """Write the file onto one cluster's nodes. ⛔ Does not restart.

        202: this runs Ansible across the fleet and takes longer than a request
        should. The screen polls.
        """
        return catalogs().deploy(
            principal, catalog_id,
            cluster=str(body.get("cluster") or ""),
            action=str(body.get("action") or "deploy"),
            reason=body.get("reason"))
