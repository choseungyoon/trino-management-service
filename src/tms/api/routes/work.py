"""The work board: status, requests, comments.

⛔ Only a `request` can be created here. Decisions and requirements start in
their own documents; a board that could mint one would put a decision record
outside the file that owns them.

The board owns status; the document each item points at owns the reasoning,
and the document wins where they disagree. `/work.md` stays because it is the
only way to read the board from outside the network it lives in.

Python 3.9 compatible.
"""

from typing import Any, Dict, Optional

from tms.api.routes.deps import Deps


def register(app, deps: Deps) -> None:
    from fastapi import Body, Depends, Query
    from fastapi.responses import PlainTextResponse

    from tms.api.permissions import Principal

    principal_of = deps.current_principal

    def board():
        return deps.require("board")

    @app.get("/api/v1/work")
    def get_board(kind: Optional[str] = Query(None),
                  principal: Principal = Depends(principal_of)):
        return board().board(principal, kind=kind)

    @app.get("/api/v1/work/{key}")
    def get_item(key: str, principal: Principal = Depends(principal_of)):
        return board().item(principal, key)

    @app.get("/api/v1/work.md", response_class=PlainTextResponse)
    def export_markdown(principal: Principal = Depends(principal_of)):
        """The board as the file that gets committed.

        Not a convenience: the board lives in a database inside the corporate
        network, and whoever is told to read it before starting work is
        usually outside it.
        """
        return board().export_markdown(principal)

    @app.post("/api/v1/work", status_code=201)
    def raise_request(body: Dict[str, Any] = Body(...),
                      principal: Principal = Depends(principal_of)):
        return board().raise_request(
            principal, title=str(body.get("title") or ""),
            body=body.get("body") or "")

    @app.post("/api/v1/work/{key}/comments", status_code=201)
    def comment(key: str, body: Dict[str, Any] = Body(...),
                principal: Principal = Depends(principal_of)):
        return board().comment(principal, key, body=str(body.get("body") or ""))

    @app.put("/api/v1/work/{key}/status")
    def set_status(key: str, body: Dict[str, Any] = Body(...),
                   principal: Principal = Depends(principal_of)):
        """Move an item, optionally with a note.

        The note becomes a comment rather than a field: what someone wrote
        when they moved it belongs in the thread with everything else that
        happened to the item.
        """
        service = board()
        note = str(body.get("note") or "").strip()
        if note:
            service.comment(principal, key, body=note)
        return service.set_status(principal, key, str(body.get("status") or ""))
