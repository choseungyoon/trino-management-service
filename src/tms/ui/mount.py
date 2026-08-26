"""Serving the built React console from FastAPI.

⛔ No Node process at runtime. `frontend/` is built on a developer's machine
and the result is committed, because deployment is `git pull` + `pip install`
onto a host that has no toolchain on it. See DECISIONS.md D-016.

Mounted at /app while the server-rendered console still owns /. It moves to /
when that one is deleted - two consoles in parallel is what the transition is
supposed to avoid, so this address is temporary by design.

Python 3.9 compatible.
"""

import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
MOUNT_PATH = "/app"


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
                 "Build it with `npm --prefix frontend run build`.", path)
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

    @app.get(path, include_in_schema=False)
    @app.get(path + "/{spa_path:path}", include_in_schema=False)
    def console(spa_path: str = ""):
        """Every in-app address returns the same document.

        Routing happens in the browser, so a deep link or a refresh must not
        404 - the server does not know which paths the client router owns.
        """
        return FileResponse(index, headers={"Cache-Control": "no-store"})

    return path
