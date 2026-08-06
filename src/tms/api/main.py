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

from tms.api.errors import ApiError, Unauthenticated
from tms.api.permissions import Principal
from tms.api.services import TmsService
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


def create_app(config: Optional[Config] = None, service: Optional[TmsService] = None):
    from fastapi import Body, Depends, FastAPI, Query, Request
    from fastapi.responses import JSONResponse

    if config is None:
        config = load_config(os.environ.get("TMS_CONFIG", DEFAULT_CONFIG_PATH))

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
        """Resolve the caller.

        Real authentication (LDAP/AD, sessions) is V9. Until then the identity
        comes from the reverse proxy, and an unauthenticated request is refused
        rather than defaulted to a role - defaulting is how a read-only console
        acquires write access by accident.
        """
        principal = getattr(request.state, "principal", None)
        if principal is not None:
            return principal
        username = request.headers.get("X-Auth-User")
        roles = [r for r in (request.headers.get("X-Auth-Roles") or "").split(",") if r]
        if not username or not roles:
            raise Unauthenticated("인증 정보가 없다")
        return Principal(username, roles, ip=request.client.host if request.client else None)

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
