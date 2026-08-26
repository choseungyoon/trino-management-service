"""The read-mostly screens: workload, gateway, health events, audit export.

Small enough that one module beats four. What they share is that a client
cannot tell "no data" from "TMS could not look" unless the payload says so -
each response carries that distinction rather than collapsing it into an empty
list.

Python 3.9 compatible.
"""

from typing import Any, Dict, Optional

from tms.api.routes.deps import Deps


def register(app, deps: Deps) -> None:
    from fastapi import Body, Depends, Query

    from tms.api.permissions import Principal

    principal_of = deps.current_principal
    service = deps.service

    @app.get("/api/v1/clusters/{cluster}/workload")
    def workload(cluster: str, principal: Principal = Depends(principal_of)):
        """Resource groups as the coordinator currently sees them.

        Ordering and the bottleneck diagnosis come back computed: which group
        is the constraint is a judgement about the numbers, not a formatting
        choice, and two clients reaching different verdicts is worse than one.
        """
        return service.get_workload(principal, cluster)

    @app.get("/api/v1/gateway")
    def gateway(principal: Principal = Depends(principal_of)):
        """Backends and routing, as the Gateway reports them.

        ⛔ Read-only. Activating or deactivating a backend is reachable only
        through the restart sequence, which drains queries first.
        """
        return service.get_gateway(principal)

    @app.get("/api/v1/clusters/{cluster}/health/events")
    def health_events(cluster: str, limit: int = Query(20, ge=1, le=500),
                      principal: Principal = Depends(principal_of)):
        """Confirmed state transitions, newest first.

        Only debounced transitions are stored, so this reads as an event log
        rather than a spike feed.
        """
        return {"events": service.list_health_events(principal, cluster,
                                                     limit=limit)}

    @app.post("/api/v1/audit/export")
    def export_audit(body: Dict[str, Any] = Body(...),
                     principal: Principal = Depends(principal_of)):
        """⛔ Exporting the audit log is itself an audited action.

        POST rather than GET for that reason: it needs a reason and it writes
        a record. If nobody records who pulled the whole log, it is not an
        audit system.
        """
        filters = {k: v for k, v in (body or {}).items()
                   if k not in ("reason", "request_id")}
        return service.export_audit(principal, reason=body.get("reason"),
                                    request_id=body.get("request_id"), **filters)
