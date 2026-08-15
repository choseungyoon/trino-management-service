"""A local TMS you can actually click through, and optionally host.

The screenshot script takes its shots and shuts the server down, which is right
for a capture run and useless for looking around. This keeps one up until you
stop it.

Same harness as the browser tests: in-memory repositories, a stub Trino, no
PostgreSQL and no real cluster anywhere near it. Every screen is populated -
including the resource group editor, where the writes really do apply (to
memory) and the real validation rules really do refuse.

    <venv>/bin/python -m tests.browser.demo [port]

## Locally

Serves HTTPS with a throwaway certificate, because the session cookie is
`Secure` and a browser will not store it over plain HTTP. Your browser will warn
about the certificate; continue past it.

## Hosted

Set `TMS_DEMO_TLS=0` and the server speaks plain HTTP on `0.0.0.0`, for
platforms that terminate TLS in front of the process (Fly.io, Render, Railway).
The cookie stays `Secure`, which is correct: the browser only ever sees the
proxy's HTTPS.

⛔ Set `TMS_DEMO_PASSWORD` and `TMS_DEMO_SESSION_SECRET` before hosting. The
defaults are committed to a public repository, so leaving them is the same as
having no password at all.

Python 3.9 compatible.
"""

import os
import sys
import tempfile

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.browser.harness import (  # noqa: E402
    PASSWORD,
    USER,
    _make_cert,
    build_app,
)

DEFAULT_PORT = 8443


def main(port=None):
    import uvicorn

    port = int(port or os.environ.get("PORT") or DEFAULT_PORT)
    password = os.environ.get("TMS_DEMO_PASSWORD") or PASSWORD
    secret = os.environ.get("TMS_DEMO_SESSION_SECRET") or None
    tls = os.environ.get("TMS_DEMO_TLS", "1") != "0"
    host = "127.0.0.1" if tls else "0.0.0.0"  # noqa: S104 - hosted behind a proxy

    if not tls and password == PASSWORD:
        # Refusing rather than warning: a warning in a deploy log is a warning
        # nobody reads, and the failure mode is an open console on the internet.
        raise SystemExit(
            "TMS_DEMO_PASSWORD is unset and the default is public (it is in "
            "this repository). Set it before hosting this anywhere.")

    app, _trino = build_app(workload_enabled=True, resource_groups=True,
                            password=password, session_secret=secret)

    scheme = "https" if tls else "http"
    print("")
    print("  TMS demo   {}://{}:{}".format(scheme, host, port))
    print("  sign in    {} / {}".format(
        USER, PASSWORD if password == PASSWORD else "(TMS_DEMO_PASSWORD)"))
    print("")
    print("  Resource Groups is at /clusters/prod-a/resource-groups")
    print("  prod-b deliberately has no rows loaded, so you can see that state.")
    print("")
    print("  Nothing is persisted; restarting resets the tree.")
    print("")

    if not tls:
        uvicorn.run(app, host=host, port=port, log_level="warning",
                    proxy_headers=True, forwarded_allow_ips="*")
        return

    # A self-signed key still being a key, this one has no business outliving
    # the process.
    key, crt = _make_cert(tempfile.mkdtemp(prefix="tms-demo-"))
    uvicorn.run(app, host=host, port=port, log_level="warning",
                ssl_keyfile=key, ssl_certfile=crt)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
