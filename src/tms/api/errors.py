"""API error taxonomy.

Codes are part of the contract in docs/API_R1.md section 0-1. The distinctions
that matter operationally:

* REASON_REQUIRED (400) is a malformed request. AUDIT_UNAVAILABLE (503) is
  infrastructure. Collapsing them would send an operator to check the database
  when they actually forgot to type a reason.
* UPSTREAM_UNAVAILABLE (503) means Trino is unreachable. AUDIT_UNAVAILABLE (503)
  means the action was never attempted. Same status, very different meaning, so
  the code carries it.

Python 3.9 compatible.
"""

from typing import Any, Dict, Optional


class ApiError(Exception):
    code = "INTERNAL_ERROR"
    status = 500

    def __init__(
        self,
        message: str,
        request_id: Optional[str] = None,
        advice: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.request_id = request_id
        self.advice = advice

    def to_payload(self) -> Dict[str, Any]:
        error: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.request_id:
            error["request_id"] = self.request_id
        if self.advice:
            error["advice"] = self.advice
        return {"error": error}


class Unauthenticated(ApiError):
    code = "UNAUTHENTICATED"
    status = 401


class Forbidden(ApiError):
    code = "FORBIDDEN"
    status = 403


class ReasonRequiredError(ApiError):
    code = "REASON_REQUIRED"
    status = 400


class NotFound(ApiError):
    code = "NOT_FOUND"
    status = 404


class UpstreamUnavailable(ApiError):
    """Trino could not be reached, or refused us."""

    code = "UPSTREAM_UNAVAILABLE"
    status = 503


class AuditUnavailableError(ApiError):
    """The audit store is down, so the write action was never attempted.

    Distinct from UPSTREAM_UNAVAILABLE on purpose: nothing happened to the
    cluster, and the operator should look at the TMS database.
    """

    code = "AUDIT_UNAVAILABLE"
    status = 503


class InvalidRequest(ApiError):
    code = "INVALID_REQUEST"
    status = 400
