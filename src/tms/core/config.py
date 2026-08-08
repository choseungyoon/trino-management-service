"""Configuration loading for TMS.

Two files are merged: `config.yaml` (tracked in git, no secrets) and
`config.secret.yaml` (gitignored). Environment variables win over both so that
systemd can inject credentials through an EnvironmentFile without any secret
ever touching the repository - which is PUBLIC (DECISIONS.md D-002).

Secret values are wrapped in `Secret` so that a stray log line, traceback or
f-string cannot leak them.

Plain dataclasses are used rather than pydantic: the collector must be able to
validate its configuration on hosts where only the runtime dependencies are
installed, and this keeps the module importable and testable on its own.

Python 3.9 compatible - no `X | Y` unions, no `match`.
"""

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

ENV_PREFIX = "TMS_"

# Environment variable -> dotted path in the merged configuration mapping.
ENV_OVERRIDES = {
    "TMS_TRINO_PASSWORD": ("trino", "password"),
    "TMS_DATABASE_URL": ("database", "url"),
    "TMS_LDAP_BIND_PASSWORD": ("ldap", "bind_password"),
    "TMS_SESSION_SECRET": ("portal", "session_secret"),
    "TMS_GATEWAY_PASSWORD": ("gateway", "password"),
}


class ConfigError(Exception):
    """Raised when configuration is missing or internally inconsistent."""


class Secret:
    """A string that refuses to render itself.

    `str(secret)` and `repr(secret)` both return a mask; the real value is only
    reachable through `.reveal()`. This makes accidental disclosure an explicit
    act rather than a side effect of logging a config object.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __bool__(self) -> bool:
        return bool(self._value)

    def __repr__(self) -> str:
        return "Secret(***)"

    def __str__(self) -> str:
        return "***"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Secret):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._value)


@dataclass(frozen=True)
class ClusterConfig:
    name: str
    coordinator_url: str
    expected_workers: int
    trino_ui_url: str = ""


@dataclass(frozen=True)
class TrinoConfig:
    user: str
    password: Secret
    verify_tls: bool = True
    connect_timeout_seconds: float = 2.0
    read_timeout_seconds: float = 5.0
    write_timeout_seconds: float = 10.0
    read_retries: int = 2
    circuit_breaker_failures: int = 5
    circuit_breaker_reset_seconds: float = 30.0


@dataclass(frozen=True)
class CollectorConfig:
    query_poll_interval_seconds: float = 5.0
    jmx_poll_interval_seconds: float = 15.0
    info_poll_interval_seconds: float = 30.0
    stale_threshold_seconds: float = 30.0
    query_text_max_bytes: int = 4096
    response_backoff_bytes: int = 5_000_000
    response_backoff_interval_seconds: float = 10.0


@dataclass(frozen=True)
class TrinoFacts:
    """Empirically verified facts about the target Trino version.

    These are configuration rather than constants so that a version upgrade that
    changes the behaviour is a one-line edit instead of a code change.
    """

    # Verified 2026-08-06: a 12-worker cluster reports ActiveNodeCount == 13.
    coordinator_counted_in_active_nodes: bool = True


@dataclass(frozen=True)
class GatewayConfig:
    enabled: bool = False
    base_url: str = ""
    # Gateway has no read-only role: the `API` role that can list backends can
    # also change them (TRINO_VERIFIED.md T2-3). Treat this like the Trino
    # credential, not like a monitoring token.
    user: str = ""
    password: Secret = field(default_factory=lambda: Secret(""))
    poll_interval_seconds: float = 30.0


@dataclass(frozen=True)
class WorkloadConfig:
    """Resource group collection (FR-WORKLOAD).

    Off by default. Collecting groups costs one MBean registry enumeration plus
    one read per group on every poll, and NFR-PERF-03 has still only been
    measured on a laptop against an idle single node - a number the measurement
    itself records as a lower bound. Turning this on before that is re-measured
    in production would add load on exactly the process whose load budget is
    unverified. Enable it after TODO.md A-1.
    """

    enabled: bool = False
    poll_interval_seconds: float = 15.0


@dataclass(frozen=True)
class HealthConfig:
    stabilization_polls: int = 3
    long_running_query_seconds: float = 300.0
    thresholds: Dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class DeeplinkConfig:
    log_template: str = ""
    log_padding_seconds: int = 300
    query_history_url_template: str = ""
    query_history_home_url: str = ""
    grafana_cluster_dashboard: str = ""
    superset_url: str = ""


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8500


@dataclass(frozen=True)
class PortalConfig:
    session_idle_timeout_minutes: int = 30
    session_absolute_timeout_hours: int = 12
    # Signs session tokens. Must be identical on every tms-api replica, or a
    # user's session breaks whenever the load balancer moves them.
    session_secret: Secret = field(default_factory=lambda: Secret(""))
    # Temporary local accounts until AD integration (D-007). Values are
    # {username: {password_hash, roles, must_change_password}}.
    local_users: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Config:
    clusters: List[ClusterConfig]
    trino: TrinoConfig
    database_url: Secret
    collector: CollectorConfig
    trino_facts: TrinoFacts
    gateway: GatewayConfig
    workload: WorkloadConfig
    health: HealthConfig
    deeplinks: DeeplinkConfig
    portal: PortalConfig
    server: ServerConfig

    def cluster(self, name: str) -> ClusterConfig:
        for cluster in self.clusters:
            if cluster.name == name:
                return cluster
        raise KeyError(name)

    @property
    def cluster_names(self) -> List[str]:
        return [c.name for c in self.clusters]


def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _current_user_description() -> str:
    """'uid 993 (tms)' - who the process actually is, for permission errors."""
    uid = getattr(os, "geteuid", lambda: None)()
    if uid is None:  # pragma: no cover - non-POSIX
        return "this process"
    name = None
    try:
        import pwd

        name = pwd.getpwuid(uid).pw_name
    except Exception:  # noqa: BLE001 - no passwd entry is not an error here
        pass
    return "uid {} ({})".format(uid, name) if name else "uid {}".format(uid)


def _secret_file_present(path: str) -> bool:
    """Does the secret file exist?

    Not os.path.exists: that returns False when the *directory* cannot be
    traversed, which silently drops every credential and surfaces later as a
    baffling "session secret is required". Refuse to guess - if we cannot tell
    whether the file is there, say so.
    """
    try:
        os.stat(path)
    except FileNotFoundError:
        return False
    except PermissionError:
        raise ConfigError(
            "{path}: cannot be checked - permission denied on a parent directory. "
            "This process runs as {who}. Every credential lives in this file, so it "
            "must not be skipped silently. Make the directory traversable:  "
            "sudo chmod o+x <parent dir>  (or chown it to the service user)".format(
                path=path, who=_current_user_description()
            )
        ) from None
    return True


def _read_yaml(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
    except PermissionError:
        # Secret files are 0600 by design, so this is nearly always an
        # ownership mismatch: the file was created by root but the service runs
        # as its own user. Say who we are and what to run - the bare errno sends
        # people hunting through systemd hardening directives instead.
        raise ConfigError(
            "{path}: permission denied. This process runs as {who} and cannot read "
            "the file. If it was created with sudo it is probably owned by root. "
            "Fix with:  sudo chown <service-user>:<service-group> {path} "
            "&& sudo chmod 600 {path}  (the parent directory must also be "
            "traversable by that user)".format(path=path, who=_current_user_description())
        ) from None
    except IsADirectoryError:
        raise ConfigError("{}: expected a file, found a directory".format(path)) from None
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigError("{}: expected a mapping at the top level".format(path))
    return loaded


def _apply_env(raw: Dict[str, Any], environ: Dict[str, str]) -> Dict[str, Any]:
    result = dict(raw)
    for env_name, path in ENV_OVERRIDES.items():
        value = environ.get(env_name)
        if value is None or value == "":
            continue
        section, key = path
        section_map = dict(result.get(section) or {})
        section_map[key] = value
        result[section] = section_map
    return result


def _require(raw: Dict[str, Any], section: str, key: str, where: str) -> Any:
    value = (raw.get(section) or {}).get(key)
    if value is None or value == "":
        raise ConfigError(
            "{}.{} is required - set it in {} or the matching TMS_* variable".format(
                section, key, where
            )
        )
    return value


def _build_clusters(raw: Dict[str, Any]) -> List[ClusterConfig]:
    entries = raw.get("clusters") or []
    if not entries:
        raise ConfigError("clusters: at least one cluster must be configured")
    clusters: List[ClusterConfig] = []
    seen = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ConfigError("clusters[{}]: expected a mapping".format(index))
        name = entry.get("name")
        url = entry.get("coordinator_url")
        if not name:
            raise ConfigError("clusters[{}].name is required".format(index))
        if not url:
            raise ConfigError("clusters[{}].coordinator_url is required".format(name))
        if name in seen:
            raise ConfigError("clusters: duplicate cluster name {!r}".format(name))
        seen.add(name)
        workers = entry.get("expected_workers")
        if not isinstance(workers, int) or workers < 0:
            raise ConfigError(
                "clusters[{}].expected_workers must be a non-negative integer".format(name)
            )
        clusters.append(
            ClusterConfig(
                name=name,
                coordinator_url=str(url).rstrip("/"),
                expected_workers=workers,
                trino_ui_url=str(entry.get("trino_ui_url") or ""),
            )
        )
    return clusters


def build_config(raw: Dict[str, Any], where: str = "config.secret.yaml") -> Config:
    """Turn an already-merged mapping into a validated Config."""
    trino_raw = raw.get("trino") or {}
    collector_raw = raw.get("collector") or {}
    facts_raw = raw.get("trino_facts") or {}
    gateway_raw = raw.get("gateway") or {}
    health_raw = raw.get("health") or {}
    deeplinks_raw = raw.get("deeplinks") or {}
    portal_raw = raw.get("portal") or {}
    server_raw = raw.get("server") or {}

    gateway = GatewayConfig(
        enabled=bool(gateway_raw.get("enabled", False)),
        base_url=str(gateway_raw.get("base_url") or "").rstrip("/"),
        user=str(gateway_raw.get("user") or ""),
        password=Secret(str(gateway_raw.get("password") or "")),
        poll_interval_seconds=float(gateway_raw.get("poll_interval_seconds", 30)),
    )
    if gateway.enabled and not gateway.base_url:
        raise ConfigError("gateway.enabled is true but gateway.base_url is empty")

    workload_raw = raw.get("workload") or {}
    workload = WorkloadConfig(
        enabled=bool(workload_raw.get("enabled", False)),
        poll_interval_seconds=float(workload_raw.get("poll_interval_seconds", 15)),
    )
    if workload.poll_interval_seconds <= 0:
        raise ConfigError("workload.poll_interval_seconds must be positive")

    collector = CollectorConfig(
        query_poll_interval_seconds=float(
            collector_raw.get("query_poll_interval_seconds", 5)
        ),
        jmx_poll_interval_seconds=float(collector_raw.get("jmx_poll_interval_seconds", 15)),
        info_poll_interval_seconds=float(collector_raw.get("info_poll_interval_seconds", 30)),
        stale_threshold_seconds=float(collector_raw.get("stale_threshold_seconds", 30)),
        query_text_max_bytes=int(collector_raw.get("query_text_max_bytes", 4096)),
        response_backoff_bytes=int(collector_raw.get("response_backoff_bytes", 5_000_000)),
        response_backoff_interval_seconds=float(
            collector_raw.get("response_backoff_interval_seconds", 10)
        ),
    )
    if collector.query_poll_interval_seconds <= 0:
        raise ConfigError("collector.query_poll_interval_seconds must be positive")
    if collector.stale_threshold_seconds < collector.query_poll_interval_seconds:
        # Otherwise every snapshot is stale the moment it is written.
        raise ConfigError(
            "collector.stale_threshold_seconds ({}) must be >= "
            "query_poll_interval_seconds ({})".format(
                collector.stale_threshold_seconds, collector.query_poll_interval_seconds
            )
        )

    deeplinks_log = deeplinks_raw.get("log") or {}
    deeplinks_history = deeplinks_raw.get("query_history") or {}
    deeplinks_grafana = deeplinks_raw.get("grafana") or {}

    return Config(
        clusters=_build_clusters(raw),
        trino=TrinoConfig(
            user=str(_require(raw, "trino", "user", where)),
            password=Secret(str(_require(raw, "trino", "password", where))),
            verify_tls=bool(trino_raw.get("verify_tls", True)),
            connect_timeout_seconds=float(trino_raw.get("connect_timeout_seconds", 2)),
            read_timeout_seconds=float(trino_raw.get("read_timeout_seconds", 5)),
            write_timeout_seconds=float(trino_raw.get("write_timeout_seconds", 10)),
            read_retries=int(trino_raw.get("read_retries", 2)),
            circuit_breaker_failures=int(trino_raw.get("circuit_breaker_failures", 5)),
            circuit_breaker_reset_seconds=float(
                trino_raw.get("circuit_breaker_reset_seconds", 30)
            ),
        ),
        database_url=Secret(str(_require(raw, "database", "url", where))),
        collector=collector,
        trino_facts=TrinoFacts(
            coordinator_counted_in_active_nodes=bool(
                facts_raw.get("coordinator_counted_in_active_nodes", True)
            )
        ),
        gateway=gateway,
        workload=workload,
        health=HealthConfig(
            stabilization_polls=int(health_raw.get("stabilization_polls", 3)),
            long_running_query_seconds=float(health_raw.get("long_running_query_seconds", 300)),
            thresholds=dict(health_raw.get("thresholds") or {}),
        ),
        deeplinks=DeeplinkConfig(
            log_template=str(deeplinks_log.get("template") or ""),
            log_padding_seconds=int(deeplinks_log.get("padding_seconds", 300)),
            query_history_url_template=str(deeplinks_history.get("query_url_template") or ""),
            query_history_home_url=str(deeplinks_history.get("home_url") or ""),
            grafana_cluster_dashboard=str(deeplinks_grafana.get("cluster_dashboard") or ""),
            superset_url=str(deeplinks_raw.get("superset_url") or ""),
        ),
        portal=PortalConfig(
            session_idle_timeout_minutes=int(portal_raw.get("session_idle_timeout_minutes", 30)),
            session_absolute_timeout_hours=int(
                portal_raw.get("session_absolute_timeout_hours", 12)
            ),
            session_secret=Secret(str(portal_raw.get("session_secret") or "")),
            local_users=dict(portal_raw.get("local_users") or {}),
        ),
        server=ServerConfig(
            host=str(server_raw.get("host") or "127.0.0.1"),
            port=int(server_raw.get("port", 8500)),
        ),
    )


def load_config(
    config_path: str,
    secret_path: Optional[str] = None,
    environ: Optional[Dict[str, str]] = None,
) -> Config:
    """Load config.yaml, overlay config.secret.yaml, then the environment."""
    if environ is None:
        environ = dict(os.environ)

    raw = _read_yaml(config_path)

    if secret_path is None:
        secret_path = os.path.join(os.path.dirname(config_path), "config.secret.yaml")
    if _secret_file_present(secret_path):
        raw = _deep_merge(raw, _read_yaml(secret_path))

    raw = _apply_env(raw, environ)
    return build_config(raw, where=secret_path)
