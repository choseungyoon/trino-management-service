"""The safe restart sequence.

⛔ The server owns the order. Stop intake, wait for queries to drain, confirm
empty, restart, verify, put it back - and every step refuses unless the one
before it finished. A client cannot skip a step by calling the next one, and
none of these endpoints exists to let it try.

⛔ There is no standalone "stop intake" endpoint, and there must never be one.
Blocking traffic is reachable only as step 1 of this sequence; a separate
toggle would be the way around the drain that follows it.

Python 3.9 compatible.
"""

from typing import Any, Dict

from tms.api.routes.deps import Deps


def register(app, deps: Deps) -> None:
    from fastapi import Body, Depends, Query

    from tms.api.permissions import Principal

    principal_of = deps.current_principal

    def restarts():
        return deps.require("restarts")

    @app.get("/api/v1/restarts")
    def recent(limit: int = Query(20, ge=1, le=200),
               principal: Principal = Depends(principal_of)):
        """Recent sequences, and whichever are still in flight.

        `active` is what the console follows around: a cluster held out of
        rotation is invisible on every other screen - the ones that remain
        look healthy while traffic is being refused.
        """
        from tms.ops.sequence import checklist

        service = restarts()
        return {
            "recent": service.recent(limit=limit),
            "active": service.active(),
            # ⛔ The same source the live checklist comes from. A client that
            # wrote its own copy of the six steps would eventually describe a
            # procedure the code no longer follows, on the one screen that
            # must not lie about the order.
            "preview": checklist(),
        }

    @app.get("/api/v1/restarts/{sequence_id}")
    def get_sequence(sequence_id: int, principal: Principal = Depends(principal_of)):
        """One sequence, re-observed.

        Reading refreshes what the coordinator says, so the step a caller sees
        is the step the server would allow - not the one it allowed last time
        somebody looked.
        """
        return restarts().get(principal, sequence_id)

    @app.post("/api/v1/clusters/{cluster}/restarts", status_code=201)
    def start(cluster: str, body: Dict[str, Any] = Body(...),
              principal: Principal = Depends(principal_of)):
        """Step 1: stop new queries reaching the cluster."""
        return restarts().start(principal, cluster, reason=body.get("reason"))

    @app.post("/api/v1/restarts/{sequence_id}/force-drain")
    def force_drain(sequence_id: int, body: Dict[str, Any] = Body(...),
                    principal: Principal = Depends(principal_of)):
        """Declare it drained while queries are still running.

        Its own reason, separate from the one that started the sequence: this
        overrides a check rather than following it, and the record has to say
        which it was.
        """
        return restarts().force_drain(principal, sequence_id,
                                      override_reason=body.get("reason"))

    @app.post("/api/v1/restarts/{sequence_id}/restart")
    def execute(sequence_id: int, principal: Principal = Depends(principal_of)):
        """Run the restart, where TMS is configured to run it."""
        return restarts().restart(principal, sequence_id)

    @app.post("/api/v1/restarts/{sequence_id}/restarted")
    def mark_restarted(sequence_id: int,
                       principal: Principal = Depends(principal_of)):
        """The operator restarted it themselves. Records who said so."""
        return restarts().mark_restarted(principal, sequence_id)

    @app.post("/api/v1/restarts/{sequence_id}/complete")
    def complete(sequence_id: int, principal: Principal = Depends(principal_of)):
        """Final step: put the cluster back in rotation."""
        return restarts().complete(principal, sequence_id)

    @app.post("/api/v1/restarts/{sequence_id}/abort")
    def abort(sequence_id: int, body: Dict[str, Any] = Body(...),
              principal: Principal = Depends(principal_of)):
        """⛔ Abort restores traffic. It is "put it back", not "stop".

        A sequence abandoned without this leaves a cluster out of rotation
        with nobody watching it.
        """
        return restarts().abort(principal, sequence_id,
                                note=str(body.get("note") or ""))
