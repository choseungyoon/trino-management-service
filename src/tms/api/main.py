"""FastAPI wiring for tms-api.

Deliberately thin. Every decision - permissions, staleness, audit enforcement,
upstream failure handling - lives in services.py so it can be tested without an
HTTP server. This module only translates HTTP to method calls and ApiError to
status codes.

FastAPI is imported inside create_app() so the service layer stays importable
in environments where the web dependencies are not installed.

Python 3.9 compatible.
"""

import logging
import os
import sys
from typing import Any, List, Optional

from tms.api.errors import (
    ApiError,
    Forbidden,
    InvalidRequest,
    TooManyAttempts,
    Unauthenticated,
)
from tms.api.permissions import Principal
from tms.api.services import TmsService
from tms.core.localauth import AccountLocked, AuthError, LocalAuthenticator, build_users
from tms.core.passwords import PasswordError
from tms.core.sessions import SessionCodec, SessionError, SessionExpired
from tms.clients.circuit import CircuitBreaker
from tms.clients.transport import HttpxTransport
from tms.clients.trino import TrinoClient
from tms.collector.postgres import PostgresSnapshotRepository
from tms.core.audit import AuditGuard
from tms.core.audit_postgres import PostgresAuditRepository
from tms.core.config import Config, load_config

log = logging.getLogger("tms.api")

DEFAULT_CONFIG_PATH = "/opt/tms/config/config.yaml"


def build_trino_clients(config: Config) -> dict:
    clients = {}
    for cluster in config.clusters:
        clients[cluster.name] = TrinoClient(
            base_url=cluster.coordinator_url,
            user=config.trino.user,
            password=config.trino.password.reveal(),
            transport=HttpxTransport(verify_tls=config.trino.verify_tls),
            verify_tls=config.trino.verify_tls,
            connect_timeout=config.trino.connect_timeout_seconds,
            read_timeout=config.trino.read_timeout_seconds,
            write_timeout=config.trino.write_timeout_seconds,
            read_retries=config.trino.read_retries,
            breaker=CircuitBreaker(
                failure_threshold=config.trino.circuit_breaker_failures,
                reset_seconds=config.trino.circuit_breaker_reset_seconds,
            ),
        )
    return clients


SESSION_COOKIE = "tms_session"


def create_app(config: Optional[Config] = None, service: Optional[TmsService] = None):
    from fastapi import Body, Depends, FastAPI, Query, Request, Response
    from fastapi.responses import JSONResponse

    if config is None:
        config = load_config(os.environ.get("TMS_CONFIG", DEFAULT_CONFIG_PATH))

    authenticator = LocalAuthenticator(build_users(config.portal.local_users))
    codec = None
    if authenticator.enabled:
        # Fail fast rather than minting an ephemeral secret: an ephemeral one
        # breaks login on every restart and across replicas, silently.
        codec = SessionCodec(
            secret=config.portal.session_secret.reveal(),
            idle_timeout_seconds=config.portal.session_idle_timeout_minutes * 60,
            absolute_timeout_seconds=config.portal.session_absolute_timeout_hours * 3600,
        )
        log.warning(
            "local account authentication is enabled (%d account(s)). This is a "
            "temporary mode - see DECISIONS.md D-007. Replace it with AD.",
            len(authenticator.users),
        )

    if service is None:
        snapshots = PostgresSnapshotRepository(config.database_url.reveal())
        audit_repository = PostgresAuditRepository(config.database_url.reveal())
        service = TmsService(
            config=config,
            repository=snapshots,
            audit_guard=AuditGuard(audit_repository),
            audit_repository=audit_repository,
            trino_clients=build_trino_clients(config),
        )

    app = FastAPI(title="TMS", version="0.1.0", docs_url=None, redoc_url=None)

    # ------------------------------------------------------------- identity

    def current_principal(request: Request) -> Principal:
        """Resolve the caller from the session cookie.

        An unauthenticated request is refused, never defaulted to a role.
        Defaulting is how a read-only console acquires write access by accident.
        """
        principal = getattr(request.state, "principal", None)
        if principal is not None:
            return principal

        if codec is None:
            raise Unauthenticated(
                "인증이 구성되지 않았다. portal.local_users 를 설정하라"
            )

        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            raise Unauthenticated("로그인이 필요하다")
        try:
            claims = codec.verify(token)
        except SessionExpired as exc:
            raise Unauthenticated("세션이 만료되었다: {}".format(exc))
        except SessionError:
            raise Unauthenticated("세션이 올바르지 않다")

        if claims.get("must_change_password") and request.url.path not in (
            "/api/v1/password",
            "/api/v1/logout",
            "/api/v1/me",
        ):
            # A temporary password must be replaced before it can be used to do
            # anything, otherwise "temporary" means "permanent in practice".
            raise Forbidden("임시 비밀번호를 먼저 변경해야 한다 (PUT /api/v1/password)")

        request.state.session_claims = claims
        return Principal(
            claims["username"],
            claims["roles"],
            ip=request.client.host if request.client else None,
        )

    def _set_session_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            samesite="strict",
            # TLS is mandatory (NFR-SEC-01), so the cookie is never sent in clear.
            secure=True,
            max_age=config.portal.session_absolute_timeout_hours * 3600,
            path="/",
        )

    @app.middleware("http")
    async def slide_session(request: Request, call_next):
        """Extend the idle window on activity, leaving the absolute deadline.

        Skips any response that already issued a session cookie. Without that
        check this middleware overwrites the handler's cookie with one rebuilt
        from the *request's* claims - so a successful password change was
        immediately undone, leaving the caller stuck behind the
        must_change_password gate forever. Found by running it, not by reading it.
        """
        response = await call_next(request)
        if codec is None or response.status_code >= 400:
            return response
        claims = getattr(request.state, "session_claims", None)
        if not claims:
            return response
        already_set = any(
            value.startswith(SESSION_COOKIE + "=")
            for value in response.headers.getlist("set-cookie")
        )
        if already_set:
            return response
        _set_session_cookie(response, codec.refresh(claims))
        return response

    # ------------------------------------------------------------ auth routes

    @app.post("/api/v1/login")
    def login(response: Response, request: Request, body: dict = Body(...)):
        if codec is None:
            raise Unauthenticated("인증이 구성되지 않았다")
        username = str(body.get("username") or "")
        password = str(body.get("password") or "")
        try:
            user = authenticator.authenticate(username, password)
        except AccountLocked as exc:
            raise TooManyAttempts(str(exc))
        except AuthError as exc:
            log.warning(
                "failed login for %r from %s",
                username,
                request.client.host if request.client else "unknown",
            )
            raise Unauthenticated(str(exc))

        token = codec.issue(
            user.username, user.roles, must_change_password=user.must_change_password
        )
        _set_session_cookie(response, token)
        return {
            "user": user.username,
            "roles": user.roles,
            "must_change_password": user.must_change_password,
        }

    @app.post("/api/v1/logout")
    def logout(response: Response):
        response.delete_cookie(SESSION_COOKIE, path="/")
        # Stateless tokens cannot be revoked server-side; the idle timeout is
        # what bounds a stolen one. Documented in DECISIONS.md D-007.
        return {"logged_out": True}

    @app.put("/api/v1/password")
    def change_password(
        response: Response,
        body: dict = Body(...),
        principal: Principal = Depends(current_principal),
    ):
        if codec is None:
            raise Unauthenticated("인증이 구성되지 않았다")
        try:
            new_hash = authenticator.change_password(
                principal.username,
                str(body.get("current_password") or ""),
                str(body.get("new_password") or ""),
            )
        except AuthError as exc:
            raise Unauthenticated(str(exc))
        except PasswordError as exc:
            raise InvalidRequest(str(exc))

        _set_session_cookie(
            response, codec.issue(principal.username, principal.roles)
        )
        # The process cannot rewrite a gitignored config file it does not own,
        # so the operator persists the new hash. Without this the change is lost
        # on restart, and saying so is better than pretending otherwise.
        return {
            "changed": True,
            "password_hash": new_hash,
            "persist_note": (
                "config.secret.yaml 의 portal.local_users.{}.password_hash 를 이 값으로 "
                "교체하고 must_change_password 를 제거하라. 재시작 전까지만 유효하다."
            ).format(principal.username),
        }

    @app.exception_handler(ApiError)
    async def api_error_handler(_request, exc: ApiError):
        return JSONResponse(status_code=exc.status, content=exc.to_payload())

    # -------------------------------------------------------------- portal

    @app.get("/api/v1/me")
    def me(principal: Principal = Depends(current_principal)):
        return service.me(principal)

    @app.get("/api/v1/links")
    def links(principal: Principal = Depends(current_principal)):
        return service.links(principal)

    # --------------------------------------------------------------- health

    @app.get("/api/v1/clusters")
    def clusters(principal: Principal = Depends(current_principal)):
        return service.list_clusters(principal)

    @app.get("/api/v1/clusters/{cluster}/health")
    def health(cluster: str, principal: Principal = Depends(current_principal)):
        return service.get_health(principal, cluster)

    @app.patch("/api/v1/clusters/{cluster}/health/tests/{test_id}")
    def patch_health_test(
        cluster: str,
        test_id: str,
        body: dict = Body(...),
        principal: Principal = Depends(current_principal),
    ):
        return service.update_health_test(
            principal,
            cluster,
            test_id,
            reason=body.get("reason"),
            enabled=body.get("enabled"),
            thresholds=body.get("thresholds"),
        )

    @app.patch("/api/v1/clusters/{cluster}/health/rollup")
    def patch_health_rollup(
        cluster: str,
        body: dict = Body(...),
        principal: Principal = Depends(current_principal),
    ):
        return service.update_health_rollup(
            principal, cluster, enabled=bool(body.get("enabled")), reason=body.get("reason")
        )

    # -------------------------------------------------------------- queries

    @app.get("/api/v1/clusters/{cluster}/queries")
    def list_queries(
        cluster: str,
        state: Optional[List[str]] = Query(None),
        user: Optional[str] = None,
        min_elapsed_seconds: Optional[float] = None,
        resource_group: Optional[str] = None,
        limit: int = 100,
        principal: Principal = Depends(current_principal),
    ):
        return service.list_queries(
            principal,
            cluster,
            state=state,
            user=user,
            min_elapsed_seconds=min_elapsed_seconds,
            resource_group=resource_group,
            limit=limit,
        )

    @app.get("/api/v1/clusters/{cluster}/queries/{query_id}")
    def get_query(
        cluster: str, query_id: str, principal: Principal = Depends(current_principal)
    ):
        return service.get_query(principal, cluster, query_id)

    @app.post("/api/v1/clusters/{cluster}/queries/{query_id}/kill")
    def kill_query(
        cluster: str,
        query_id: str,
        body: dict = Body(...),
        principal: Principal = Depends(current_principal),
    ):
        return service.kill_query(principal, cluster, query_id, reason=body.get("reason"))

    # ---------------------------------------------------------------- audit

    @app.get("/api/v1/audit")
    def audit(
        actor: Optional[str] = None,
        action_type: Optional[str] = None,
        target_kind: Optional[str] = None,
        target_id: Optional[str] = None,
        outcome: Optional[str] = None,
        limit: int = 100,
        principal: Principal = Depends(current_principal),
    ):
        return service.search_audit(
            principal,
            actor=actor,
            action_type=action_type,
            target_kind=target_kind,
            target_id=target_id,
            outcome=outcome,
            limit=limit,
        )

    @app.get("/api/v1/audit/export")
    def audit_export(
        reason: str = Query(..., description="내보내기 사유. 이 호출도 감사된다"),
        limit: int = 500,
        principal: Principal = Depends(current_principal),
    ):
        return service.export_audit(principal, reason=reason, limit=limit)

    # ------------------------------------------------------------ operations

    @app.get("/health")
    def tms_health():
        return {"status": "ok"}

    @app.get("/ready")
    def tms_ready():
        return {"status": "ready"}

    return app


def run() -> int:
    logging.basicConfig(
        level=os.environ.get("TMS_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        import uvicorn
    except ImportError:
        log.error("uvicorn is not installed")
        return 2
    config = load_config(os.environ.get("TMS_CONFIG", DEFAULT_CONFIG_PATH))
    uvicorn.run(
        create_app(config), host=config.server.host, port=config.server.port, log_config=None
    )
    return 0


if __name__ == "__main__":
    sys.exit(run())
