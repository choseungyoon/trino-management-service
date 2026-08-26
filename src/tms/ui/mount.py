"""Serving the built React console from FastAPI.

⛔ No Node process at runtime. `frontend/` is built on a developer's machine
and the result is committed, because deployment is `git pull` + `pip install`
onto a host that has no toolchain on it. See DECISIONS.md D-016.

Mounted at /, which it now owns outright: the server-rendered console was
deleted once all twelve screens had been ported (D-016).

⛔ Mounted last, and its catch-all must never match /api/. Every in-app address
returns index.html, so a route registered after it would be unreachable.

Python 3.9 compatible.
"""

import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
MOUNT_PATH = ""


def available() -> bool:
    """Has the console been built?

    A checkout without the build is a normal state for someone working on the
    backend, and it must not stop tms-api from starting.
    """
    return os.path.isfile(os.path.join(ASSETS, "index.html"))


def mount(app, path: str = MOUNT_PATH) -> Optional[str]:
    """Serve the console, or say why it is not there. Returns the path served."""
    if not available():
        log.info("the React console is not built; %s is not served. "
                 "Build it with `npm --prefix frontend run build`.", path or "/")
        return None

    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    index = os.path.join(ASSETS, "index.html")

    # Hashed filenames, so these can be cached hard. index.html cannot: it is
    # the file that names the current hashes, and a stale copy of it points at
    # a bundle that no longer exists.
    app.mount(path + "/static",
              StaticFiles(directory=os.path.join(ASSETS, "static")),
              name="console-static")

    from tms.api.errors import NotFound

    @app.get(path + "/", include_in_schema=False)
    @app.get(path + "/{spa_path:path}", include_in_schema=False)
    def console(spa_path: str = ""):
        """Every in-app address returns the same document.

        Routing happens in the browser, so a deep link or a refresh must not
        404 - the server does not know which paths the client router owns.
        """
        # ⛔ Except under /api/. An unknown API path must answer as an API,
        # not hand back an HTML page that a client will try to parse as JSON -
        # that turns "you called the wrong endpoint" into a parse error.
        if spa_path.startswith("api/"):
            raise NotFound("No such endpoint: /{}".format(spa_path))
        return FileResponse(index, headers={"Cache-Control": "no-store"})

    return path or "/"
