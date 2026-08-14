"""Server-rendered UI routes.

These render the same TmsService the JSON API uses, so a screen can never show
something the API would refuse. Every write goes through the service's audit
guard — there is no UI-only path to a kill.

Server-rendered rather than a SPA because the operator value here is a fast,
dense, link-shareable page, and because the whole thing must keep working when
a coordinator is down. JavaScript upgrades the experience (drawer, auto-refresh)
and never gates it: every action is a real form post to a real URL.

Python 3.9 compatible.
"""

import logging
import os
from typing import Any, Dict, List, Optional

from tms.api.errors import ApiError, Forbidden, NotFound, Unauthenticated
from tms.api.permissions import (
    EXPORT_AUDIT,
    KILL_QUERY,
    MANAGE_HEALTH,
    VIEW_AUDIT,
    Principal,
)
from tms.ops.sequence import checklist as sequence_checklist
from tms.web import views
from tms.web.formatting import FILTERS

log = logging.getLogger("tms.web")

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

THEME_COOKIE = "tms_theme"
# Pages a user with an unchanged temporary password may still reach.
TEMP_PASSWORD_ALLOWED = ("/account", "/account/password", "/logout", "/ui/theme")


def build_templates():
    from fastapi.templating import Jinja2Templates

    templates = Jinja2Templates(directory=TEMPLATE_DIR)
    templates.env.filters.update(FILTERS)
    templates.env.trim_blocks = True
    templates.env.lstrip_blocks = True
    return templates


def register(app, service, config, authenticator, codec, session_cookie: str,
             restarts=None, fleet=None) -> None:
    """Mount the UI on an existing FastAPI app.

    `restarts` is the FR-CO-02 sequence service and `fleet` the FR-FLEET one.
    Either may be None when it cannot run (no Gateway, no inventory). None is a
    real state the screens handle, not an error - the console still shows
    everything else.
    """
    from fastapi import Form, Request
    from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
    from fastapi.staticfiles import StaticFiles

    from tms.core.localauth import AccountLocked, AuthError
    from tms.core.passwords import PasswordError
    from tms.core.sessions import SessionError, SessionExpired

    templates = build_templates()
    app.mount("/ui/static", StaticFiles(directory=STATIC_DIR), name="web_static")

    cluster_names = config.cluster_names
    environment = os.environ.get("TMS_ENVIRONMENT", "")

    # ── session helpers ────────────────────────────────────────────────

    def session_claims(request: Request) -> Optional[Dict[str, Any]]:
        if codec is None:
            return None
        token = request.cookies.get(session_cookie)
        if not token:
            return None
        try:
            return codec.verify(token)
        except (SessionExpired, SessionError):
            return None

    def principal_or_redirect(request: Request):
        """Returns (principal, claims) or a RedirectResponse to /login."""
        claims = session_claims(request)
        if claims is None:
            target = request.url.path
            if request.url.query:
                target += "?" + request.url.query
            return RedirectResponse("/login?next=" + _quote(target), status_code=303), None
        principal = Principal(
            claims["username"],
            claims["roles"],
            ip=request.client.host if request.client else None,
        )
        return principal, claims

    def theme_of(request: Request) -> str:
        return "light" if request.cookies.get(THEME_COOKIE) == "light" else "dark"

    # How often each live page reloads itself, in seconds. Matched to the
    # collector's own cadence: refreshing faster than data arrives just burns
    # queries and makes the page jump for nothing, and refreshing slower leaves
    # an operator staring at a number that quietly went out of date.
    #
    # Pages not listed here never auto-refresh. The audit log is deliberately
    # among them - it is a record being read, not a dashboard being watched,
    # and reloading it under someone mid-investigation loses their place.
    collector_cfg = config.collector
    refresh_by_page = {
        "overview": max(int(collector_cfg.query_poll_interval_seconds), 5),
        "queries": max(int(collector_cfg.query_poll_interval_seconds), 5),
        "health": max(int(collector_cfg.jmx_poll_interval_seconds), 10),
        "workload": max(int(config.workload.poll_interval_seconds), 10),
        "gateway": max(int(config.gateway.poll_interval_seconds), 15),
        # A draining worker takes minutes; the operator watches this page while
        # it does, so it tracks the fleet poll rather than sitting frozen.
        "fleet": max(int(config.fleet.poll_interval_seconds), 15),
        # "restart" is deliberately absent. That page refreshes itself by
        # swapping two panels (tms.js), because a whole-page reload every few
        # seconds would throw away the operator's place in a progress log that
        # is still being written to.
        #
        # "resource-groups" is absent too, for the opposite reason: it shows
        # configuration, which changes when a person changes it rather than on
        # a timer. Polling it would put a query per interval on the database
        # Trino itself reads to admit queries, to re-fetch an answer that is
        # almost always identical to the last one.
    }

    def base_context(request: Request, principal: Principal, page: str) -> Dict[str, Any]:
        try:
            links = views.link_rows(service.links(principal))
        except ApiError:
            links = []
        return {
            "request": request,
            "principal": {"user": principal.username, "roles": principal.roles,
                          "capabilities": principal.capabilities},
            "page": page,
            "theme": theme_of(request),
            "environment": environment,
            "links": links,
            "cluster_names": cluster_names,
            # The nav hides the Gateway link when the integration is off - a
            # link to a page that can only say "disabled" is noise.
            "gateway_enabled": config.gateway.enabled,
            "restarts_enabled": restarts is not None,
            "fleet_enabled": fleet is not None,
            "resource_groups_enabled": config.resource_groups.enabled,
            # A cluster held out of rotation is invisible on every other
            # screen: the remaining clusters are green, so the console looks
            # healthy while traffic is being refused. The banner follows the
            # operator around until the sequence is finished.
            "active_restarts": restarts.active() if restarts is not None else [],
            "flash": _take_flash(request),
            # Drives data-refresh in base.html, which tms.js reads. Without it
            # the auto-refresh timer never starts and every screen is frozen
            # until the operator reloads by hand.
            "refresh_seconds": refresh_by_page.get(page, 0),
        }

    def render(name: str, context: Dict[str, Any], status_code: int = 200) -> HTMLResponse:
        # Starlette takes the request first now; the legacy (name, context) form
        # silently binds the template name to the request slot and fails deep in
        # Jinja with "unhashable type: dict".
        request = context.get("request")
        return templates.TemplateResponse(request, name, context, status_code=status_code)

    def _issue_session(response, username: str, roles: List[str], must_change: bool) -> None:
        response.set_cookie(
            session_cookie,
            codec.issue(username, roles, must_change_password=must_change),
            httponly=True, samesite="strict", secure=True, path="/",
            max_age=config.portal.session_absolute_timeout_hours * 3600,
        )

    def _flash(response, level: str, message: str) -> None:
        response.set_cookie("tms_flash", "{}|{}".format(level, message),
                            max_age=15, path="/", samesite="strict")

    def _take_flash(request: Request) -> Optional[Dict[str, str]]:
        raw = request.cookies.get("tms_flash")
        if not raw or "|" not in raw:
            return None
        level, message = raw.split("|", 1)
        return {"level": level, "message": message}

    # ── auth ───────────────────────────────────────────────────────────

    @app.get("/login", response_class=HTMLResponse, include_in_schema=False)
    def login_form(request: Request, next: str = "/"):
        if session_claims(request) is not None:
            return RedirectResponse(next or "/", status_code=303)
        return render("login.html", {"request": request, "theme": theme_of(request),
                                     "environment": environment, "next_url": next,
                                     "error": None, "username": ""})

    @app.post("/login", include_in_schema=False)
    def login_submit(request: Request, username: str = Form(""), password: str = Form(""),
                     next: str = Form("/")):
        if codec is None:
            return render("login.html", {"request": request, "theme": theme_of(request),
                                         "environment": environment, "next_url": next,
                                         "error": "Local accounts are not configured.",
                                         "username": username}, status_code=503)
        try:
            user = authenticator.authenticate(username, password)
        except AccountLocked as exc:
            return render("login.html", {"request": request, "theme": theme_of(request),
                                         "environment": environment, "next_url": next,
                                         "error": str(exc), "username": username},
                          status_code=429)
        except AuthError:
            log.warning("failed UI login for %r from %s", username,
                        request.client.host if request.client else "unknown")
            # Deliberately identical for unknown user and wrong password.
            return render("login.html", {"request": request, "theme": theme_of(request),
                                         "environment": environment, "next_url": next,
                                         "error": "Incorrect username or password.",
                                         "username": username}, status_code=401)

        destination = "/account" if user.must_change_password else (next or "/")
        response = RedirectResponse(destination, status_code=303)
        _issue_session(response, user.username, user.roles, user.must_change_password)
        return response

    @app.get("/logout", include_in_schema=False)
    @app.post("/logout", include_in_schema=False)
    def logout():
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(session_cookie, path="/")
        return response

    @app.post("/ui/theme", include_in_schema=False)
    def toggle_theme(request: Request, next: str = Form("/")):
        response = RedirectResponse(next or "/", status_code=303)
        response.set_cookie(THEME_COOKIE, "light" if theme_of(request) == "dark" else "dark",
                            max_age=60 * 60 * 24 * 365, path="/", samesite="strict")
        return response

    # ── temp-password gate ─────────────────────────────────────────────

    @app.middleware("http")
    async def temp_password_gate(request: Request, call_next):
        """A temporary password may only be used to replace itself.

        Without this the word "temporary" means nothing: the holder browses
        indefinitely on a credential someone else generated for them.
        """
        path = request.url.path
        if path.startswith(("/ui/static", "/api/", "/login", "/health", "/ready", "/metrics")):
            return await call_next(request)
        claims = session_claims(request)
        if claims and claims.get("must_change_password") and path not in TEMP_PASSWORD_ALLOWED:
            return RedirectResponse("/account", status_code=303)
        return await call_next(request)

    # ── account ────────────────────────────────────────────────────────

    @app.get("/account", response_class=HTMLResponse, include_in_schema=False)
    def account(request: Request):
        principal, claims = principal_or_redirect(request)
        if claims is None:
            return principal
        context = base_context(request, principal, "account")
        context.update({"must_change": bool(claims.get("must_change_password")),
                        "error": None, "new_hash": None})
        return render("account.html", context)

    @app.post("/account/password", response_class=HTMLResponse, include_in_schema=False)
    def change_password(request: Request, current_password: str = Form(""),
                        new_password: str = Form("")):
        principal, claims = principal_or_redirect(request)
        if claims is None:
            return principal
        context = base_context(request, principal, "account")
        context.update({"must_change": bool(claims.get("must_change_password")),
                        "error": None, "new_hash": None})
        try:
            new_hash = authenticator.change_password(principal.username, current_password,
                                                     new_password)
        except AuthError:
            context["error"] = "Current password is incorrect."
            return render("account.html", context, status_code=401)
        except PasswordError as exc:
            context["error"] = str(exc)
            return render("account.html", context, status_code=400)

        context["new_hash"] = new_hash
        context["must_change"] = False
        response = render("account.html", context)
        _issue_session(response, principal.username, principal.roles, must_change=False)
        return response

    # ── overview ───────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def overview(request: Request):
        principal, claims = principal_or_redirect(request)
        if claims is None:
            return principal

        clusters = []
        oldest: Optional[str] = None
        any_stale = False
        for cluster in config.clusters:
            health_env = service.get_health(principal, cluster.name)
            try:
                queries_env = service.list_queries(principal, cluster.name, limit=1)
            except ApiError:
                queries_env = None
            summary = views.cluster_summary(cluster.name, cluster.expected_workers,
                                            health_env, queries_env)
            clusters.append(summary)
            any_stale = any_stale or summary["stale"]
            collected = health_env.get("collected_at")
            if collected and (oldest is None or collected < oldest):
                oldest = collected

        context = base_context(request, principal, "overview")
        context.update({"clusters": clusters,
                        "envelope": {"collected_at": oldest, "stale": any_stale}})
        return render("overview.html", context)

    # ── live queries ───────────────────────────────────────────────────

    @app.get("/clusters/{cluster}/queries", response_class=HTMLResponse, include_in_schema=False)
    def cluster_queries(request: Request, cluster: str, state: Optional[str] = None,
                        user: Optional[str] = None, long_running: Optional[str] = None,
                        group: Optional[str] = None):
        return queries(request, cluster=cluster, state=state, user=user,
                       long_running=long_running, group=group)

    @app.get("/queries", response_class=HTMLResponse, include_in_schema=False)
    def queries(request: Request, cluster: Optional[str] = None, state: Optional[str] = None,
                user: Optional[str] = None, long_running: Optional[str] = None,
                group: Optional[str] = None):
        principal, claims = principal_or_redirect(request)
        if claims is None:
            return principal

        states = views.expand_state_filter(state)
        min_elapsed = None
        if long_running:
            min_elapsed = config.health.long_running_query_seconds

        try:
            if cluster:
                envelope = service.list_queries(principal, cluster, state=states, user=user,
                                                min_elapsed_seconds=min_elapsed,
                                                resource_group=group, limit=200)
                data = envelope.get("data") or {}
                for row in data.get("queries") or []:
                    row["cluster"] = cluster
                degraded = ([{"name": cluster,
                              "unavailable_reason": data.get("unavailable_reason"),
                              "advice": data.get("advice")}]
                            if data.get("unavailable_reason") else [])
                any_trustworthy = not data.get("unavailable_reason")
            else:
                envelope = service.list_queries_all(principal, state=states, user=user,
                                                    min_elapsed_seconds=min_elapsed,
                                                    resource_group=group, limit=200)
                data = envelope.get("data") or {}
                degraded = [c for c in data.get("clusters") or [] if c.get("unavailable_reason")]
                any_trustworthy = any(
                    not c.get("unavailable_reason") for c in data.get("clusters") or []
                )
        except NotFound as exc:
            return _error_page(request, principal, exc)
        except Forbidden as exc:
            return _error_page(request, principal, exc)

        base_params = {"cluster": cluster or "", "user": user or "", "group": group or ""}
        context = base_context(request, principal, "queries")
        context.update({
            "envelope": envelope,
            "queries": data.get("queries") or [],
            "truncated": data.get("truncated"),
            "degraded_clusters": degraded,
            "any_trustworthy": any_trustworthy,
            "selected_cluster": cluster,
            "active_state": state,
            "user_filter": user,
            "group_filter": group,
            "chips": views.query_chips(data.get("summary") or {}, base_params, state,
                                       bool(long_running)),
            "can_kill": principal.can(KILL_QUERY),
            "running_counter": None,
        })
        return render("queries.html", context)

    @app.get("/clusters/{cluster}/queries/{query_id}", response_class=HTMLResponse,
             include_in_schema=False)
    def query_detail(request: Request, cluster: str, query_id: str, fragment: int = 0):
        principal, claims = principal_or_redirect(request)
        if claims is None:
            return principal
        try:
            row = _query_row(principal, cluster, query_id)
        except ApiError as exc:
            return _error_page(request, principal, exc)

        context = {"request": request, "cluster": cluster, "query": row,
                   "can_kill": principal.can(KILL_QUERY)}
        if fragment:
            return render("query_detail.html", context)
        page = base_context(request, principal, "queries")
        page.update(context)
        page["envelope"] = None
        return render("query_detail_page.html", page)

    @app.get("/clusters/{cluster}/queries/{query_id}/kill", response_class=HTMLResponse,
             include_in_schema=False)
    def kill_form(request: Request, cluster: str, query_id: str):
        principal, claims = principal_or_redirect(request)
        if claims is None:
            return principal
        if not principal.can(KILL_QUERY):
            return _error_page(request, principal, Forbidden("You cannot kill queries."))
        try:
            row = _query_row(principal, cluster, query_id)
        except ApiError as exc:
            return _error_page(request, principal, exc)
        context = base_context(request, principal, "queries")
        context.update({"cluster": cluster, "query": row, "error": None, "reason": "",
                        "envelope": None,
                        "back_url": "/clusters/" + _quote(cluster) + "/queries"})
        return render("kill_query.html", context)

    @app.post("/clusters/{cluster}/queries/{query_id}/kill", include_in_schema=False)
    def kill_submit(request: Request, cluster: str, query_id: str, reason: str = Form("")):
        principal, claims = principal_or_redirect(request)
        if claims is None:
            return principal
        try:
            service.kill_query(principal, cluster, query_id, reason=reason)
        except ApiError as exc:
            try:
                row = _query_row(principal, cluster, query_id)
            except ApiError:
                row = {"query_id": query_id}
            context = base_context(request, principal, "queries")
            context.update({"cluster": cluster, "query": row, "error": exc.message,
                            "reason": reason, "envelope": None,
                            "back_url": "/clusters/" + _quote(cluster) + "/queries"})
            return render("kill_query.html", context, status_code=exc.status)

        response = RedirectResponse("/clusters/" + _quote(cluster) + "/queries", status_code=303)
        _flash(response, "good", "Query {} killed. The reason was delivered to its owner.".format(query_id))
        return response

    def _query_row(principal: Principal, cluster: str, query_id: str) -> Dict[str, Any]:
        """Snapshot row (fast, has our derived fields) enriched with live SQL."""
        row: Dict[str, Any] = {"query_id": query_id}
        envelope = service.list_queries(principal, cluster, limit=500)
        for candidate in (envelope.get("data") or {}).get("queries") or []:
            if candidate.get("query_id") == query_id:
                row = dict(candidate)
                break
        try:
            detail = (service.get_query(principal, cluster, query_id).get("data") or {})
            row["sql"] = detail.get("query")
            row.setdefault("state", detail.get("state"))
        except ApiError:
            # A finished query is gone from the coordinator; the snapshot row is
            # still worth showing.
            row.setdefault("sql", row.get("query_preview"))
        row.setdefault("cluster", cluster)
        return row

    # ── health ─────────────────────────────────────────────────────────

    @app.get("/clusters", response_class=HTMLResponse, include_in_schema=False)
    def clusters_redirect():
        return RedirectResponse("/", status_code=303)

    @app.get("/gateway", response_class=HTMLResponse, include_in_schema=False)
    def gateway_page(request: Request):
        principal, claims = principal_or_redirect(request)
        if claims is None:
            return principal
        try:
            envelope = service.get_gateway(principal)
        except ApiError as exc:
            return _error_page(request, principal, exc)

        context = base_context(request, principal, "gateway")
        context.update({"envelope": envelope, "gateway": envelope.get("data") or {}})
        return render("gateway.html", context)

    @app.get("/clusters/{cluster}/workload", response_class=HTMLResponse,
             include_in_schema=False)
    def workload(request: Request, cluster: str, sort: Optional[str] = None,
                 desc: int = 1):
        principal, claims = principal_or_redirect(request)
        if claims is None:
            return principal
        try:
            envelope = service.get_workload(principal, cluster)
        except ApiError as exc:
            return _error_page(request, principal, exc)

        data = envelope.get("data") or {}
        rows, ranked = views.order_groups(
            data.get("tree") or [], data.get("groups") or [], sort, bool(desc))
        context = base_context(request, principal, "workload")
        context.update({
            "envelope": envelope,
            "workload": data,
            "rows": rows,
            # Ranked means the hierarchy is gone: the template stops indenting
            # and says so, rather than showing a tree in an order it is not in.
            "ranked": ranked,
            "sort": sort or "",
            "sort_desc": bool(desc),
            "sort_label": views.column_label(sort),
            "columns": views.WORKLOAD_COLUMNS,
            "bottleneck_text": views.bottleneck_text,
            "selected_cluster": cluster,
        })
        return render("workload.html", context)

    @app.get("/clusters/{cluster}/resource-groups", response_class=HTMLResponse,
             include_in_schema=False)
    def resource_groups(request: Request, cluster: str):
        """The configured tree (FR-WL-07), read only.

        No envelope and no staleness banner: this comes from the store rather
        than a collector snapshot, so it is current by construction. The one
        part that can be stale is the JMX column, and the payload says so
        separately.
        """
        principal, claims = principal_or_redirect(request)
        if claims is None:
            return principal
        try:
            result = service.get_resource_group_config(principal, cluster)
        except ApiError as exc:
            return _error_page(request, principal, exc)

        context = base_context(request, principal, "resource-groups")
        context.update({
            "groups": result.get("data") or {},
            "selected_cluster": cluster,
        })
        return render("resource_groups.html", context)

    @app.get("/clusters/{cluster}/health", response_class=HTMLResponse, include_in_schema=False)
    def health(request: Request, cluster: str):
        principal, claims = principal_or_redirect(request)
        if claims is None:
            return principal
        selected = cluster
        try:
            envelope = service.get_health(principal, selected)
        except ApiError as exc:
            return _error_page(request, principal, exc)

        data = views.health_view(envelope)
        try:
            events = service.list_health_events(principal, selected, limit=8)
        except (ApiError, AttributeError):
            events = []

        context = base_context(request, principal, "health")
        context.update({
            "envelope": envelope,
            "health": data,
            "counts": views.state_counts(data.get("tests") or []),
            "events": events,
            "selected_cluster": selected,
            "can_manage": principal.can(MANAGE_HEALTH),
            "stabilization_polls": config.health.stabilization_polls,
        })
        return render("health.html", context)

    @app.get("/clusters/{cluster}/health/tests/{test_id}", response_class=HTMLResponse,
             include_in_schema=False)
    def health_test_form(request: Request, cluster: str, test_id: str, enable: int = 0):
        principal, claims = principal_or_redirect(request)
        if claims is None:
            return principal
        if not principal.can(MANAGE_HEALTH):
            return _error_page(request, principal, Forbidden("You cannot change health tests."))
        context = base_context(request, principal, "health")
        context.update({"cluster": cluster, "test_id": test_id, "test_name": None,
                        "enable": bool(enable), "error": None, "reason": "", "envelope": None})
        return render("health_test.html", context)

    @app.post("/clusters/{cluster}/health/tests/{test_id}", include_in_schema=False)
    def health_test_submit(request: Request, cluster: str, test_id: str,
                           enabled: str = Form("false"), reason: str = Form("")):
        principal, claims = principal_or_redirect(request)
        if claims is None:
            return principal
        enable = enabled.lower() == "true"
        try:
            service.update_health_test(principal, cluster, test_id, reason=reason, enabled=enable)
        except ApiError as exc:
            context = base_context(request, principal, "health")
            context.update({"cluster": cluster, "test_id": test_id, "test_name": None,
                            "enable": enable, "error": exc.message, "reason": reason,
                            "envelope": None})
            return render("health_test.html", context, status_code=exc.status)
        response = RedirectResponse("/clusters/" + _quote(cluster) + "/health", status_code=303)
        _flash(response, "good", "{} {} for {}.".format(
            test_id, "enabled" if enable else "disabled", cluster))
        return response

    @app.post("/clusters/{cluster}/health/rollup", include_in_schema=False)
    def health_rollup(request: Request, cluster: str, enabled: str = Form("true")):
        principal, claims = principal_or_redirect(request)
        if claims is None:
            return principal
        # The roll-up toggle is a write like any other; send it through the form
        # so it carries a reason rather than firing from a bare switch.
        return RedirectResponse(
            "/clusters/{}/health/tests/{}?enable={}".format(_quote(cluster), "*",
                                                   1 if enabled.lower() == "true" else 0),
            status_code=303,
        )

    # ── fleet (FR-FL-01, FR-FL-03) ─────────────────────────────────────

    @app.get("/clusters/{cluster}/fleet", response_class=HTMLResponse,
             include_in_schema=False)
    def fleet_page(request: Request, cluster: str, host: Optional[str] = None,
                   error: Optional[str] = None):
        principal, claims = principal_or_redirect(request)
        if claims is None:
            return principal
        if fleet is None:
            return _error_page(request, principal, NotFound(
                "Fleet collection is off (fleet.enabled)."))
        try:
            envelope = fleet.get_fleet(principal, cluster)
        except ApiError as exc:
            return _error_page(request, principal, exc)

        data = envelope.get("data") or {}
        context = base_context(request, principal, "fleet")
        context.update({
            "envelope": envelope,
            "fleet": data,
            "nodes": data.get("nodes") or [],
            "selected_cluster": cluster,
            "can_manage": principal.can(MANAGE_HEALTH),
            # Set when the confirm form is open for one node.
            "confirm_host": host,
            "error": error,
        })
        return render("fleet.html", context)

    @app.post("/clusters/{cluster}/fleet/{host}/shutdown", include_in_schema=False)
    def fleet_shutdown(request: Request, cluster: str, host: str,
                       reason: str = Form("")):
        principal, claims = principal_or_redirect(request)
        if claims is None:
            return principal
        if fleet is None:
            return _error_page(request, principal, NotFound("Fleet is not configured."))
        try:
            result = fleet.shutdown_node(principal, cluster, host, reason=reason)
        except ApiError as exc:
            # Back to the form with the node still selected, so the reason the
            # operator typed is not the only thing they lose.
            return RedirectResponse(
                "/clusters/{}/fleet?host={}&error={}".format(
                    _quote(cluster), _quote(host), _quote(exc.message)),
                status_code=303)
        except Exception as exc:  # noqa: BLE001
            log.exception("shutdown of %s failed", host)
            return RedirectResponse(
                "/clusters/{}/fleet?error={}".format(_quote(cluster), _quote(str(exc))),
                status_code=303)

        response = RedirectResponse("/clusters/" + _quote(cluster) + "/fleet",
                                    status_code=303)
        _flash(response, "good", result["note"])
        return response

    # ── safe restart sequence (FR-CO-02) ───────────────────────────────
    #
    # One page for the whole procedure. The left column is the checklist -
    # where the sequence has got to and what it is waiting for - and the right
    # column is the live log, the way an operator watches a playbook run.
    #
    # Every step is its own POST to its own URL with the sequence in a known
    # state, so a stale tab cannot replay step 4 onto a cluster that has since
    # moved on: the state machine refuses it. Without JavaScript the page
    # simply reloads on a timer.

    def _restart_page(request: Request, principal: Principal, cluster: str,
                      error: Optional[str] = None, status_code: int = 200,
                      sequence_id: Optional[Any] = None):
        context = base_context(request, principal, "restart")
        context.update({
            "selected_cluster": cluster,
            "error": error,
            "envelope": None,
            "sequence": None,
            "can_manage": principal.can(MANAGE_HEALTH),
            "recent": [],
            "preview_steps": sequence_checklist(),
        })
        if restarts is None:
            return render("restart.html", context, status_code=status_code)

        try:
            if sequence_id is not None:
                context["sequence"] = restarts.refresh(principal, sequence_id)
            else:
                active = [s for s in restarts.active() if s["cluster"] == cluster]
                if active:
                    context["sequence"] = restarts.refresh(principal, active[0]["id"])
        except ApiError as exc:
            context["error"] = context["error"] or exc.message

        if context["sequence"] is None:
            # Nothing in flight: the page is the start form plus history.
            context["recent"] = [s for s in restarts.recent(10)
                                 if s["cluster"] == cluster]
        return render("restart.html", context, status_code=status_code)

    @app.get("/clusters/{cluster}/restart", response_class=HTMLResponse,
             include_in_schema=False)
    def restart_page(request: Request, cluster: str):
        principal, claims = principal_or_redirect(request)
        if claims is None:
            return principal
        if cluster not in cluster_names:
            return _error_page(request, principal, NotFound(
                "Unknown cluster: {}".format(cluster)))
        return _restart_page(request, principal, cluster)

    @app.get("/restarts/{sequence_id}", response_class=HTMLResponse,
             include_in_schema=False)
    def restart_sequence_page(request: Request, sequence_id: int, fragment: int = 0):
        """One sequence, live or finished. `fragment=1` returns just the panels.

        The fragment is what the live view polls: replacing two panels keeps
        the operator's scroll position in a log that is still being written to,
        which a full reload would throw away every few seconds.
        """
        principal, claims = principal_or_redirect(request)
        if claims is None:
            return principal
        if restarts is None:
            return _error_page(request, principal, NotFound("Restarts are not configured."))
        try:
            sequence = restarts.refresh(principal, sequence_id)
        except ApiError as exc:
            return _error_page(request, principal, exc)

        if fragment:
            return render("restart_live.html", {
                "request": request, "sequence": sequence,
                "can_manage": principal.can(MANAGE_HEALTH),
            })
        return _restart_page(request, principal, sequence["cluster"],
                             sequence_id=sequence_id)

    @app.post("/clusters/{cluster}/restart", include_in_schema=False)
    def restart_start(request: Request, cluster: str, reason: str = Form("")):
        principal, claims = principal_or_redirect(request)
        if claims is None:
            return principal
        if restarts is None:
            return _error_page(request, principal, NotFound("Restarts are not configured."))
        try:
            sequence = restarts.start(principal, cluster, reason=reason)
        except ApiError as exc:
            return _restart_page(request, principal, cluster, error=exc.message,
                                 status_code=exc.status)
        except Exception as exc:  # noqa: BLE001
            # The service closes the sequence out when it could not stop
            # traffic, so nothing is left half-started; say what happened.
            log.exception("could not begin a restart of %s", cluster)
            return _restart_page(
                request, principal, cluster, status_code=502,
                error="Could not stop traffic to {}, so no restart was started: "
                      "{}".format(cluster, exc))
        return RedirectResponse("/restarts/{}".format(sequence["id"]), status_code=303)

    def _step(request: Request, sequence_id: int, call, success: str,
              **kwargs):
        """Run one step and come back to the sequence page.

        Always a redirect, so a refresh never re-posts the step - repeating
        "restart now" because someone hit F5 is exactly the accident this
        sequence is built to prevent.
        """
        principal, claims = principal_or_redirect(request)
        if claims is None:
            return principal
        if restarts is None:
            return _error_page(request, principal, NotFound("Restarts are not configured."))

        response = RedirectResponse("/restarts/{}".format(sequence_id), status_code=303)
        try:
            call(principal, sequence_id, **kwargs)
        except ApiError as exc:
            _flash(response, "bad", exc.message)
            return response
        except Exception as exc:  # noqa: BLE001
            # ⛔ Never an error page. When a step fails the cluster is usually
            # still out of rotation, and what to do about it is written in the
            # sequence's own log - which a stack trace would hide.
            log.exception("restart step failed for sequence %s", sequence_id)
            _flash(response, "bad", "{}. The restart is still open - see the "
                                    "progress log.".format(exc))
            return response
        if success:
            _flash(response, "good", success)
        return response

    @app.post("/restarts/{sequence_id}/force-drain", include_in_schema=False)
    def restart_force_drain(request: Request, sequence_id: int, reason: str = Form("")):
        return _step(request, sequence_id, restarts.force_drain if restarts else None,
                     "Drain overridden. The running queries will be killed by the "
                     "restart.", override_reason=reason)

    @app.post("/restarts/{sequence_id}/restart", include_in_schema=False)
    def restart_execute(request: Request, sequence_id: int):
        return _step(request, sequence_id, restarts.restart if restarts else None, "")

    @app.post("/restarts/{sequence_id}/restarted", include_in_schema=False)
    def restart_mark_restarted(request: Request, sequence_id: int):
        return _step(request, sequence_id,
                     restarts.mark_restarted if restarts else None, "")

    @app.post("/restarts/{sequence_id}/complete", include_in_schema=False)
    def restart_complete(request: Request, sequence_id: int):
        return _step(request, sequence_id, restarts.complete if restarts else None,
                     "Traffic restored. The cluster is back in rotation.")

    @app.post("/restarts/{sequence_id}/abort", include_in_schema=False)
    def restart_abort(request: Request, sequence_id: int, reason: str = Form("")):
        return _step(request, sequence_id, restarts.abort if restarts else None,
                     "Aborted. Traffic has been restored.", note=reason)

    # ── audit ──────────────────────────────────────────────────────────

    @app.get("/audit", response_class=HTMLResponse, include_in_schema=False)
    def audit(request: Request, action_type: Optional[str] = None, actor: Optional[str] = None):
        principal, claims = principal_or_redirect(request)
        if claims is None:
            return principal
        if not principal.can(VIEW_AUDIT):
            return _error_page(request, principal, Forbidden("You cannot view the audit log."))

        result = service.search_audit(principal, action_type=action_type, actor=actor, limit=200)
        everything = service.search_audit(principal, limit=500)["records"]
        counts: Dict[str, int] = {"all": len(everything)}
        for record in everything:
            counts[record["action_type"]] = counts.get(record["action_type"], 0) + 1

        context = base_context(request, principal, "audit")
        context.update({
            "records": result["records"],
            "chips": views.audit_chips(action_type, counts),
            "action_filter": action_type,
            "actor_filter": actor,
            "can_export": principal.can(EXPORT_AUDIT),
            "envelope": None,
        })
        return render("audit.html", context)

    @app.get("/audit/export", response_class=HTMLResponse, include_in_schema=False)
    def audit_export_form(request: Request):
        principal, claims = principal_or_redirect(request)
        if claims is None:
            return principal
        if not principal.can(EXPORT_AUDIT):
            return _error_page(request, principal, Forbidden("You cannot export the audit log."))
        context = base_context(request, principal, "audit")
        context.update({"error": None, "reason": "", "envelope": None})
        return render("audit_export.html", context)

    @app.post("/audit/export", include_in_schema=False)
    def audit_export_submit(request: Request, reason: str = Form("")):
        principal, claims = principal_or_redirect(request)
        if claims is None:
            return principal
        try:
            result = service.export_audit(principal, reason=reason, limit=500)
        except ApiError as exc:
            context = base_context(request, principal, "audit")
            context.update({"error": exc.message, "reason": reason, "envelope": None})
            return render("audit_export.html", context, status_code=exc.status)

        return PlainTextResponse(
            _to_csv(result.get("rows") or []),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="tms-audit.csv"'},
        )

    # ── errors ─────────────────────────────────────────────────────────

    def _error_page(request: Request, principal: Principal, exc: ApiError):
        context = base_context(request, principal, "")
        context.update({"envelope": None, "error": exc})
        return render("error.html", context, status_code=exc.status)

    @app.exception_handler(Unauthenticated)
    async def _unauthenticated(request: Request, exc: Unauthenticated):
        if request.url.path.startswith("/api/"):
            raise exc
        return RedirectResponse("/login", status_code=303)


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def _to_csv(rows: List[Dict[str, Any]]) -> str:
    import csv
    import io

    # The header is written even with no rows. An export is evidence someone
    # asked for, and a 0-byte file cannot be told apart from a broken export -
    # "nothing matched" and "this failed" must not look identical.
    columns = ["occurred_at", "actor", "actor_roles", "actor_ip", "action_type", "target_kind",
               "target_id", "target_cluster", "reason", "outcome", "error_message", "request_id"]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        flat = dict(row)
        if isinstance(flat.get("actor_roles"), list):
            flat["actor_roles"] = ",".join(flat["actor_roles"])
        writer.writerow(flat)
    return buffer.getvalue()
