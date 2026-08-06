"""Error taxonomy for outbound calls.

Every failure that reaches TMS has to answer two questions:

1. Is it transient? Only transient failures should trip a circuit breaker.
   A 403 is permanent - retrying it hides an actionable configuration problem
   behind a generic "unavailable".
2. What should the operator do? Health tests render `advice` verbatim, so a
   failure with no remedy is not allowed to reach the UI (HEALTH_TESTS.md).

The distinction matters most for `TrinoForbidden` and `MBeanNotRegistered`,
which look like outages but are really rules.json and version problems.
"""

from typing import Optional


class TrinoClientError(Exception):
    """Base class. `transient` decides circuit-breaker behaviour."""

    transient = False
    advice = ""

    def __init__(self, message: str, advice: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        if advice is not None:
            self.advice = advice


class TrinoUnavailable(TrinoClientError):
    """Connection refused, timeout, or 5xx. Transient - trips the breaker."""

    transient = True
    advice = (
        "The coordinator could not be reached. Check its systemd unit and logs — "
        "this cluster may not be accepting new queries."
    )


class CircuitOpen(TrinoUnavailable):
    """Calls are being short-circuited after repeated failures."""

    advice = (
        "Calls to this coordinator are paused after repeated failures. They resume "
        "automatically once the breaker closes. Check the coordinator."
    )


class TrinoUnauthorized(TrinoClientError):
    """401. The service account credentials are wrong or missing."""

    advice = (
        "Authentication failed (401). Check the tms-svc account and password "
        "(config.secret.yaml or TMS_TRINO_PASSWORD). Note that basic auth only "
        "works over HTTPS."
    )


class TrinoForbidden(TrinoClientError):
    """403. Authenticated but not authorised.

    Not transient: retrying never helps and tripping the breaker would bury the
    one message that tells the operator how to fix it.
    """

    advice = (
        "Authorization denied (403). Check the tms-svc grants in rules.json — "
        "JMX and /metrics need system_information: read; listing and killing "
        "queries need queries: view and queries: kill."
    )


class TrinoNotFound(TrinoClientError):
    """404. The resource is gone - a finished query, usually."""

    advice = "Not found. The query may have already finished."


class MBeanNotRegistered(TrinoClientError):
    """500 from /v1/jmx/mbean/{name}.

    Airlift's MBeanResource declares `throws JMException` and maps nothing, so a
    name that does not exist surfaces as 500 rather than 404. This bit us once
    already: the Trino 477 docs still list a FailureDetector MBean that the
    release no longer installs (TRINO_VERIFIED.md T1-7).
    """

    advice = (
        "This MBean is not registered on the server (500). A Trino upgrade may "
        "have renamed it — enumerate GET /v1/jmx/mbean and trust that list over "
        "the documentation."
    )


class TrinoProtocolError(TrinoClientError):
    """A 2xx response that could not be parsed as expected."""

    advice = "The response shape was not what we expect. Check whether Trino was upgraded."


def classify_status(status: int, path: str) -> TrinoClientError:
    """Map an HTTP status to the error that best explains what to do about it."""
    if status == 401:
        return TrinoUnauthorized("HTTP 401 for {}".format(path))
    if status == 403:
        return TrinoForbidden("HTTP 403 for {}".format(path))
    if status == 404:
        return TrinoNotFound("HTTP 404 for {}".format(path))
    if status == 500 and "/v1/jmx/mbean/" in path:
        return MBeanNotRegistered("HTTP 500 for {}".format(path))
    return TrinoUnavailable("HTTP {} for {}".format(status, path))
