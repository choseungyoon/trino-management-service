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
        "코디네이터에 도달할 수 없다. systemd 유닛 상태와 코디네이터 로그를 확인하라. "
        "이 클러스터는 신규 쿼리를 받지 못하고 있을 수 있다."
    )


class CircuitOpen(TrinoUnavailable):
    """Calls are being short-circuited after repeated failures."""

    advice = (
        "연속 실패로 이 코디네이터에 대한 호출을 일시 차단했다. "
        "차단이 풀리면 자동으로 재시도한다. 코디네이터 상태를 확인하라."
    )


class TrinoUnauthorized(TrinoClientError):
    """401. The service account credentials are wrong or missing."""

    advice = (
        "인증 실패(401). tms-svc 계정과 비밀번호를 확인하라 "
        "(config.secret.yaml 또는 TMS_TRINO_PASSWORD)."
    )


class TrinoForbidden(TrinoClientError):
    """403. Authenticated but not authorised.

    Not transient: retrying never helps and tripping the breaker would bury the
    one message that tells the operator how to fix it.
    """

    advice = (
        "인가 거부(403). rules.json 에서 tms-svc 의 권한을 확인하라 — "
        "JMX/metrics 는 system_information:read, 쿼리 조회·kill 은 "
        "queries:view / queries:kill 이 필요하다."
    )


class TrinoNotFound(TrinoClientError):
    """404. The resource is gone - a finished query, usually."""

    advice = "대상을 찾을 수 없다. 쿼리가 이미 종료되었을 수 있다."


class MBeanNotRegistered(TrinoClientError):
    """500 from /v1/jmx/mbean/{name}.

    Airlift's MBeanResource declares `throws JMException` and maps nothing, so a
    name that does not exist surfaces as 500 rather than 404. This bit us once
    already: the Trino 477 docs still list a FailureDetector MBean that the
    release no longer installs (TRINO_VERIFIED.md T1-7).
    """

    advice = (
        "MBean 이름이 이 서버에 등록되어 있지 않다(500). Trino 버전업으로 이름이 "
        "바뀌었을 수 있다. GET /v1/jmx/mbean 으로 실제 등록 목록을 확인하라. "
        "문서보다 실제 등록 목록을 신뢰할 것."
    )


class TrinoProtocolError(TrinoClientError):
    """A 2xx response that could not be parsed as expected."""

    advice = "응답 형식이 예상과 다르다. Trino 버전 변경 여부를 확인하라."


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
