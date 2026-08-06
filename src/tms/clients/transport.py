"""HTTP transport abstraction.

The Trino client depends on this interface rather than on httpx directly, so
retry, circuit-breaker and parsing behaviour can be tested without a network
stack or an installed HTTP library. The httpx implementation is imported lazily
for the same reason.

Python 3.9 compatible.
"""

from typing import Dict, Optional


class TransportError(Exception):
    """Connection refused, DNS failure, TLS failure, or timeout.

    Always transient from the caller's point of view: there is no HTTP status to
    interpret, so the client treats it as an outage.
    """


class HttpResponse:
    __slots__ = ("status", "body", "elapsed_seconds")

    def __init__(self, status: int, body: bytes, elapsed_seconds: float = 0.0) -> None:
        self.status = status
        self.body = body
        self.elapsed_seconds = elapsed_seconds

    @property
    def size_bytes(self) -> int:
        return len(self.body)

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def __repr__(self) -> str:
        return "HttpResponse(status={}, size={})".format(self.status, self.size_bytes)


class Transport:
    """Minimal synchronous HTTP interface."""

    def request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[bytes] = None,
        connect_timeout: float = 2.0,
        read_timeout: float = 5.0,
        verify_tls: bool = True,
    ) -> HttpResponse:
        raise NotImplementedError

    def close(self) -> None:
        pass


class HttpxTransport(Transport):
    """Production transport. httpx is imported on construction, not on import,
    so this module stays importable in environments without it."""

    def __init__(self, verify_tls: bool = True) -> None:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "httpx is required for HttpxTransport; install the tms package "
                "dependencies or inject a different Transport"
            ) from exc
        self._httpx = httpx
        self._client = httpx.Client(verify=verify_tls, follow_redirects=False)

    def request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[bytes] = None,
        connect_timeout: float = 2.0,
        read_timeout: float = 5.0,
        verify_tls: bool = True,
    ) -> HttpResponse:
        timeout = self._httpx.Timeout(
            connect=connect_timeout, read=read_timeout, write=read_timeout, pool=connect_timeout
        )
        try:
            response = self._client.request(
                method, url, headers=headers or {}, content=body, timeout=timeout
            )
        except self._httpx.HTTPError as exc:
            raise TransportError(str(exc)) from exc
        return HttpResponse(
            response.status_code,
            response.content,
            response.elapsed.total_seconds() if response.elapsed else 0.0,
        )

    def close(self) -> None:
        self._client.close()
