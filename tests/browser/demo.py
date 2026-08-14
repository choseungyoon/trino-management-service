"""A local TMS you can actually click through.

The screenshot script takes its shots and shuts the server down, which is right
for a capture run and useless for looking around. This keeps one up on a fixed
port until you stop it.

Same harness as the browser tests: in-memory repositories, a stub Trino, no
PostgreSQL. Every screen is populated - including the resource group editor,
where the writes really do apply (to memory) and the real validation rules
really do refuse.

    <venv>/bin/python -m tests.browser.demo [port]

HTTPS with a throwaway certificate, because the session cookie is `Secure` and
a browser will not store it over plain HTTP. Your browser will warn about the
certificate; that is expected - continue past it.

Python 3.9 compatible.
"""

import os
import ssl
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


def main(port=DEFAULT_PORT):
    import uvicorn

    app, _trino = build_app(workload_enabled=True, resource_groups=True)

    # NamedTemporaryDirectory rather than the repo: a self-signed key is still a
    # key, and this one has no business outliving the process.
    tmp = tempfile.mkdtemp(prefix="tms-demo-")
    key, crt = _make_cert(tmp)

    print("")
    print("  TMS demo   https://127.0.0.1:{}".format(port))
    print("  sign in    {} / {}".format(USER, PASSWORD))
    print("")
    print("  Resource Groups is at /clusters/prod-a/resource-groups")
    print("  prod-b deliberately has no rows loaded, so you can see that state.")
    print("  The certificate is self-signed - click through the warning.")
    print("")
    print("  Ctrl-C to stop. Nothing is persisted; restarting resets the tree.")
    print("")

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning",
                ssl_keyfile=key, ssl_certfile=crt)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT)
