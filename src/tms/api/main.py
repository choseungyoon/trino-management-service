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

DEFAULT_CONFIG_PATH = "/etc/trino-management-service/config/config.yaml"


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


def build_restart_service(config: Config, service: TmsService, config_store=None):
    """Assemble the safe restart sequence (FR-CO-02), or None if it cannot run.

    None rather than a half-built service: without the Gateway there is no way
    to stop traffic to a cluster, and a "restart" that skips that step is the
    exact incident CLAUDE.md rule 5 exists to prevent. The UI then says the
    feature is unavailable and why, instead of offering a button that would do
    something unsafe.
    """
    from tms.clients.gateway import build_gateway_client
    from tms.ops.executor import build_executor
    from tms.ops.repository import PostgresSequenceRepository
    from tms.ops.service import RestartService

    gateway_client = build_gateway_client(config)
    if gateway_client is None:
        log.info(
            "the Gateway integration is off, so cluster restarts are not "
            "available - TMS cannot stop traffic to a cluster without it")
        return None

    try:
        repository = PostgresSequenceRepository(config.database_url.reveal())
    except Exception as exc:  # noqa: BLE001
        log.error("cannot open the restart sequence store: %s", exc)
        return None

    return RestartService(
        config=config,
        repository=repository,
        snapshots=service.repository,
        gateway_client=gateway_client,
        audit_guard=service.audit,
        executor=build_executor(config),
        # Built once by the caller and shared with TmsService: one store, one
        # place that knows whether it is configured at all.
        config_store=(config_store if config_store is not None
                      else build_resource_group_store(config)),
    )


def build_resource_group_store(config: Config):
    """Trino's resource group tables, read-only, or None (D-010).

    Returning None is the honest outcome when the store is not configured: the
    restart sequence then declines to have an opinion, rather than reporting a
    healthy store it never looked at.
    """
    if not config.resource_groups.enabled:
        return None
    from tms.ops.config_store import ResourceGroupStore

    try:
        # Construction validates the schema name and nothing else - it does not
        # connect. A database that is down must surface as a blocked restart at
        # the moment someone tries, not as a feature that silently vanished at
        # startup.
        return ResourceGroupStore(
            config.database_url.reveal(), config.resource_groups.schema)
    except Exception as exc:  # noqa: BLE001
        log.error("cannot use the resource group store: %s", exc)
        return None


def build_fleet_service(config: Config, service: TmsService):
    """Fleet inventory and node lifecycle (FR-FL-01/03), or None when off.

    Unlike restarts this needs no Gateway: shutting a worker down drains it
    without touching routing, because the cluster stays up throughout.
    """
    if not getattr(config, "fleet", None) or not config.fleet.enabled:
        return None
    from tms.clients.transport import HttpxTransport
    from tms.fleet.service import FleetService

    runner, repository = build_fleet_jobs(config)

    def sql_for(cluster: str):
        # ⛔ Built per call, from the same TrinoClient the rest of TMS uses, so
        # auth, TLS and the circuit breaker are not duplicated. Handed in as a
        # factory rather than a client so FleetService never holds an open
        # SQL path it did not ask for.
        from tms.clients.sql import SqlClient

        return SqlClient(service.trino_clients[cluster])

    return FleetService(
        config=config,
        snapshots=service.repository,
        audit_guard=service.audit,
        transport_factory=lambda: HttpxTransport(verify_tls=config.trino.verify_tls),
        stale_threshold=config.collector.stale_threshold_seconds,
        job_runner=runner,
        job_repository=repository,
        sql_client_factory=sql_for,
    )


def build_fleet_jobs(config: Config):
    """The FR-FL-04 runner and its store, or (None, None).

    None when `fleet.jobs` is empty, which is the default: this uses the TMS
    host's SSH access to every node, so it appears only when an administrator
    has declared what may be run (D-009's reasoning, applied again).
    """
    if not config.fleet.jobs:
        return None, None
    from tms.fleet.jobs import JobRunner, build_jobs
    from tms.fleet.jobstore import PostgresJobRepository

    try:
        repository = PostgresJobRepository(config.database_url.reveal())
    except Exception as exc:  # noqa: BLE001
        log.error("cannot open the fleet job store, so jobs are off: %s", exc)
        return None, None

    # A row still saying RUNNING belongs to a subprocess that died with the
    # previous process. Left alone it would block the cluster's unique index
    # forever and tell an operator a playbook is still going.
    orphans = repository.reconcile_orphans()
    if orphans:
        log.warning("marked %d fleet job(s) UNKNOWN: tms-api restarted while "
                    "they were running", orphans)

    runner = JobRunner(
        jobs=build_jobs(config.fleet.jobs),
        cluster_inventories=config.fleet.inventories,
        binary=config.cluster_ops.ansible.binary,
        state_dir=config.cluster_ops.ansible.state_dir,
    )
    return runner, repository


def build_benchmark_service(config: Config, service: TmsService, gateway_client=None):
    """The FR-BM harness, or None when `benchmark.enabled` is false.

    Off by default. A benchmark takes a cluster's capacity, and the guard that
    makes that safe (FR-BM-04) needs the Gateway - so this appears only where
    an administrator has declared query sets and meant it.
    """
    if not getattr(config, "benchmark", None) or not config.benchmark.enabled:
        return None
    from tms.bench.queryset import build_query_sets
    from tms.bench.runner import BenchmarkRunner
    from tms.bench.service import BenchmarkService
    from tms.bench.store import PostgresBenchmarkRepository

    try:
        repository = PostgresBenchmarkRepository(config.database_url.reveal())
    except Exception as exc:  # noqa: BLE001
        log.error("cannot open the benchmark store, so benchmarks are off: %s", exc)
        return None

    # A row still saying RUNNING belongs to a worker thread that died with the
    # previous process; left alone it blocks the cluster's unique index and
    # tells an operator a run is still going.
    orphans = repository.reconcile_orphans()
    if orphans:
        log.warning("marked %d benchmark run(s) UNKNOWN: tms-api restarted "
                    "while they were in flight", orphans)

    def sql_for(cluster: str):
        # Its own timeout, much larger than the SQL client's default: a
        # benchmark query that takes four minutes is the measurement, and
        # timing it out at 30s would record the harness's impatience instead.
        from tms.clients.sql import SqlClient

        return SqlClient(service.trino_clients[cluster],
                         timeout_seconds=config.benchmark.timeout_seconds)

    return BenchmarkService(
        config=config,
        snapshots=service.repository,
        audit_guard=service.audit,
        repository=repository,
        runner=BenchmarkRunner(sql_client_factory=sql_for, repository=repository,
                               pause_seconds=config.benchmark.pause_seconds),
        query_sets=build_query_sets(config.benchmark.query_sets),
        gateway_client=gateway_client,
        stale_threshold=config.collector.stale_threshold_seconds,
    )


def build_board_service(config: Config):
    """The FR-BOARD work board, or None.

    None when the database will not open. The board is a planning surface, not
    part of any query path, so it disappears from the nav rather than taking
    the console down with it.
    """
    from tms.work.service import BoardService
    from tms.work.store import PostgresBoardRepository

    try:
        repository = PostgresBoardRepository(config.database_url.reveal())
    except Exception as exc:  # noqa: BLE001
        log.error("cannot open the work board store, so the board is off: %s", exc)
        return None
    return BoardService(repository)


def create_app(config: Optional[Config] = None, service: Optional[TmsService] = None,
               restarts: Optional[Any] = None, fleet: Optional[Any] = None,
               board: Optional[Any] = None, benchmark: Optional[Any] = None):
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

    config_store = build_resource_group_store(config)

    if service is None:
        snapshots = PostgresSnapshotRepository(config.database_url.reveal())
        audit_repository = PostgresAuditRepository(config.database_url.reveal())
        service = TmsService(
            config=config,
            repository=snapshots,
            audit_guard=AuditGuard(audit_repository),
            audit_repository=audit_repository,
            trino_clients=build_trino_clients(config),
            config_store=config_store,
        )

    if restarts is None:
        restarts = build_restart_service(config, service, config_store=config_store)
    if fleet is None:
        fleet = build_fleet_service(config, service)
    if board is None:
        board = build_board_service(config)
    if benchmark is None:
        from tms.clients.gateway import build_gateway_client

        # Its own client rather than reaching into the restart service: the
        # guard must be able to ask the Gateway even on a deployment where
        # restarts are not configured.
        benchmark = build_benchmark_service(config, service,
                                            gateway_client=build_gateway_client(config))

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
                "Authentication is not configured. Set portal.local_users."
            )

        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            raise Unauthenticated("Sign in required.")
        try:
            claims = codec.verify(token)
        except SessionExpired as exc:
            raise Unauthenticated("Session expired: {}".format(exc))
        except SessionError:
            raise Unauthenticated("Invalid session.")

        if claims.get("must_change_password") and request.url.path not in (
            "/api/v1/password",
            "/api/v1/logout",
            "/api/v1/me",
        ):
            # A temporary password must be replaced before it can be used to do
            # anything, otherwise "temporary" means "permanent in practice".
            raise Forbidden("Change your temporary password first (PUT /api/v1/password).")

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
            raise Unauthenticated("Authentication is not configured.")
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
            raise Unauthenticated("Authentication is not configured.")
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
                "Replace portal.local_users.{}.password_hash in config.secret.yaml with "
                "this value and remove must_change_password. Valid only until restart."
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
        reason: str = Query(..., description="Reason for the export. This call is itself audited."),
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

    # The operator console. Mounted last so its catch-all page routes never
    # shadow an /api/ path, and skipped entirely when local accounts are off —
    # a UI with no way to sign in is worse than no UI.
    if codec is not None:
        from tms.web.routes import register as register_web

        register_web(app, service, config, authenticator, codec, SESSION_COOKIE,
                     restarts=restarts, fleet=fleet, board=board,
                     benchmark=benchmark)

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
