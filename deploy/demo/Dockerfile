# The TMS demo console — synthetic data only.
#
# ⛔ This image is NOT how TMS is deployed. Production runs from systemd units
# (ops/systemd/) against a real PostgreSQL and real coordinators. This builds
# the browser-test harness: in-memory repositories, a stub Trino, and a fake
# resource group store. There is no way to point it at a cluster, which is the
# property that makes it safe to expose.
#
# See deploy/demo/README.md before hosting it anywhere.

FROM python:3.12-slim

# Nothing here writes to the image, and the app holds no secrets on disk.
RUN useradd --create-home --uid 10001 demo
WORKDIR /app

# Dependencies first so a code change does not re-resolve them.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . uvicorn

# The demo server lives with the browser tests, because it is the same harness.
COPY tests ./tests

USER demo

# Plain HTTP: every platform this targets terminates TLS in front of the
# process. The session cookie stays Secure - the browser only sees the proxy.
ENV TMS_DEMO_TLS=0 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

EXPOSE 8080

# Refuses to start unless TMS_DEMO_PASSWORD is set: the default is committed to
# a public repository, so booting without one would put an open operations
# console on the internet.
CMD ["python", "-m", "tests.browser.demo"]
