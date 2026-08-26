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
    # This cluster's `node.environment`. Trino's db resource group manager
    # matches rows on it, so it is what TMS needs to ask "does the store hold
    # configuration for this cluster" before restarting it (D-010).
    node_environment: str = ""


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
    unverified. Enable it after docs/TODO.md W-1.
    """

    enabled: bool = False
    poll_interval_seconds: float = 15.0


@dataclass(frozen=True)
class FleetConfig:
    """Node inventory and lifecycle (FR-FLEET).

    Its own section rather than borrowing `cluster_ops.ansible.inventories`,
    because seeing the fleet and restarting it are different privileges: an
    operator should be able to look at the nodes on a deployment where TMS is
    not allowed to touch them. The two usually point at the same files.

    `node_url_template` exists because an inventory carries addresses, not
    ports or schemes. Building `https://{address}:8443` by assumption would
    make every node look unreachable on a cluster that runs plain HTTP - which
    reads as an outage rather than as a misconfiguration.
    """

    enabled: bool = False
    poll_interval_seconds: float = 60.0
    inventories: Dict[str, str] = field(default_factory=dict)
    node_url_template: str = ""
    # Playbooks TMS may run on request. Empty means the feature does not
    # appear at all - running one uses the TMS host's SSH access to every node.
    #
    # ⛔ Never point one at a playbook that restarts anything. Nothing here
    # checks that the cluster was drained. tms-config-check refuses that case.
    jobs: Dict[str, Any] = field(default_factory=dict)
    # Trino needs 2 x shutdown.grace-period plus running tasks before a worker
    # exits - four minutes on the defaults. A shorter deadline times out on a
    # healthy shutdown.
    shutdown_timeout_seconds: float = 900.0


@dataclass(frozen=True)
class AnsibleConfig:
    """Where the restart playbook lives, and which inventory targets what.

    `inventories` maps a TMS cluster name to that cluster's inventory file. The
    platform team keeps one per cluster, so choosing a cluster means choosing a
    file - no host name ever reaches the command line.
    """

    playbook: str = ""
    binary: str = "ansible-playbook"
    timeout_seconds: float = 1800.0
    # ⛔ Ansible aborts at import time without a writable HOME - measured on
    # ansible-core 2.21: exit 5, "Unable to create local directories
    # '~/.ansible/tmp'". tms-api runs under ProtectHome=true, so HOME points
    # here instead.
    #
    # Must match `StateDirectory=` in the systemd unit, which takes a name
    # relative to /var/lib. They disagree silently: the restart just blocks.
    state_dir: str = "/var/lib/trino-management-service"
    inventories: Dict[str, str] = field(default_factory=dict)
    extra_vars: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ClusterOpsConfig:
    """The safe restart sequence (FR-CO-02).

    `restart_mode` decides who performs step 4, and it is deliberately
    `manual` by default. Turning it to `ansible` gives TMS SSH reach into every
    Trino node, which is a security decision an administrator makes explicitly -
    never something that happens because a package was installed.
    """

    restart_mode: str = "manual"
    drain_timeout_seconds: float = 900.0
    ansible: AnsibleConfig = field(default_factory=AnsibleConfig)


@dataclass(frozen=True)
class ResourceGroupStoreConfig:
    """Trino's `db` resource group manager tables (D-010).

    Off by default. Leaving it off means TMS assumes the `file` manager and
    makes no claim either way - it does not mean "the store is fine".

    `schema` must match `?currentSchema=` in the coordinator's
    `resource-groups.config-db-url`. The tables live in the TMS database but in
    their own schema, so a TMS migration has no path to the tables Trino reads
    to decide whether to admit a query.
    """

    enabled: bool = False
    schema: str = "trino_resource_groups"
    # Whether Trino has `etc/group-provider.properties`. TMS cannot see the
    # coordinator's filesystem, and the consequence is not obvious: without a
    # provider the groups Trino compares against are always empty, so any
    # `user_group_regex` selector is a rule that can never match. Default False
    # because that is the state a cluster starts in, and a warning about a rule
    # that does work is cheaper than silence about one that does not.
    group_provider_configured: bool = False


@dataclass(frozen=True)
class BenchmarkConfig:
    """Declared query sets and their limits (FR-BM-01).

    Off by default. A benchmark takes a real cluster's capacity, so it appears
    only where somebody switched it on and meant it.

    ⛔ **Query sets are not here.** They were, until FR-BM-06 moved them into
    the database so an administrator can add one without a deploy. A set left
    behind in YAML would be a second source for the same thing, and the one
    the console edits would win silently - so `benchmark.query_sets` is
    rejected outright rather than ignored. See DECISIONS.md D-014.

    `timeout_seconds` is per statement and much larger than the SQL client's
    30s: a benchmark query that takes four minutes is the finding, not a
    failure.
    """

    enabled: bool = False
    default_repetitions: int = 3
    max_repetitions: int = 20
    timeout_seconds: float = 600.0
    # A gap between statements so the cluster is not measured while it is
    # still finishing the previous one.
    pause_seconds: float = 1.0


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
    cluster_ops: ClusterOpsConfig
    fleet: FleetConfig
    resource_groups: ResourceGroupStoreConfig
    benchmark: BenchmarkConfig
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
                node_environment=str(entry.get("node_environment") or "").strip(),
            )
        )
    return clusters


def _build_fleet(raw: Dict[str, Any]) -> FleetConfig:
    enabled = bool(raw.get("enabled", False))
    inventories = {str(k): str(v) for k, v in (raw.get("inventories") or {}).items()}
    template = str(raw.get("node_url_template") or "")
    interval = float(raw.get("poll_interval_seconds", 60))
    timeout = float(raw.get("shutdown_timeout_seconds", 900))
    jobs = raw.get("jobs") or {}

    if jobs:
        # Validated at load so a malformed job is a startup error rather than a
        # discovery made while someone is trying to add capacity.
        from tms.fleet.jobs import JobError, build_jobs

        try:
            build_jobs(jobs)
        except JobError as exc:
            raise ConfigError("fleet.jobs: {}".format(exc))

    if enabled:
        if not inventories:
            raise ConfigError("fleet.enabled is true but fleet.inventories is empty")
        for cluster, path in sorted(inventories.items()):
            if not os.path.isabs(path):
                raise ConfigError(
                    "fleet.inventories[{}] must be an absolute path".format(cluster))
        if "{address}" not in template:
            # Refused rather than defaulted: a wrong scheme or port makes every
            # node look down, which reads as an outage instead of a typo.
            raise ConfigError(
                "fleet.node_url_template must contain {address}, "
                "e.g. 'https://{address}:8443'")
        if interval <= 0:
            raise ConfigError("fleet.poll_interval_seconds must be positive")
        if timeout <= 0:
            raise ConfigError("fleet.shutdown_timeout_seconds must be positive")

    return FleetConfig(enabled=enabled, poll_interval_seconds=interval,
                       inventories=inventories, node_url_template=template,
                       jobs=dict(jobs), shutdown_timeout_seconds=timeout)


def _build_benchmark(raw: Dict[str, Any]) -> BenchmarkConfig:
    from tms.bench.queryset import MAX_REPETITIONS

    enabled = bool(raw.get("enabled", False))

    # Refused, not ignored. Someone upgrading from before FR-BM-06 has a set
    # in this file that the console will not show, and silently dropping it
    # would look like the sets were lost.
    if raw.get("query_sets"):
        raise ConfigError(
            "benchmark.query_sets has moved into the database (FR-BM-06). "
            "Remove it from config and re-enter the sets on the benchmark "
            "query set page - they are edited there now.")

    default_reps = int(raw.get("default_repetitions", 3))
    max_reps = int(raw.get("max_repetitions", MAX_REPETITIONS))
    timeout = float(raw.get("timeout_seconds", 600))
    pause = float(raw.get("pause_seconds", 1))

    if not 1 <= max_reps <= MAX_REPETITIONS:
        raise ConfigError(
            "benchmark.max_repetitions must be between 1 and {}".format(
                MAX_REPETITIONS))
    if not 1 <= default_reps <= max_reps:
        raise ConfigError(
            "benchmark.default_repetitions must be between 1 and "
            "benchmark.max_repetitions ({})".format(max_reps))
    if timeout <= 0:
        raise ConfigError("benchmark.timeout_seconds must be positive")
    if pause < 0:
        raise ConfigError("benchmark.pause_seconds cannot be negative")

    return BenchmarkConfig(enabled=enabled,
                           default_repetitions=default_reps,
                           max_repetitions=max_reps, timeout_seconds=timeout,
                           pause_seconds=pause)


def _build_cluster_ops(raw: Dict[str, Any], whole: Dict[str, Any]) -> ClusterOpsConfig:
    """Validate the restart-execution settings, refusing half-configured ones.

    A misconfigured automated restart must fail here, at startup, rather than
    at the moment an operator is holding a deactivated cluster and needs it to
    work. Every check below is a thing that would otherwise surface mid-restart.
    """
    mode = str(raw.get("restart_mode") or "manual").strip().lower()
    if mode not in ("manual", "ansible"):
        raise ConfigError(
            "cluster_ops.restart_mode must be 'manual' or 'ansible', not {!r}".format(mode))

    ansible_raw = raw.get("ansible") or {}
    inventories = {str(k): str(v) for k, v in (ansible_raw.get("inventories") or {}).items()}
    ansible = AnsibleConfig(
        playbook=str(ansible_raw.get("playbook") or ""),
        binary=str(ansible_raw.get("binary") or "ansible-playbook"),
        timeout_seconds=float(ansible_raw.get("timeout_seconds", 1800)),
        state_dir=str(ansible_raw.get("state_dir") or "/var/lib/trino-management-service"),
        inventories=inventories,
        extra_vars={str(k): str(v) for k, v in (ansible_raw.get("extra_vars") or {}).items()},
    )

    if mode == "ansible":
        if not ansible.playbook:
            raise ConfigError(
                "cluster_ops.restart_mode is 'ansible' but cluster_ops.ansible.playbook "
                "is empty")
        if ansible.timeout_seconds <= 0:
            raise ConfigError("cluster_ops.ansible.timeout_seconds must be positive")
        # An unlisted cluster would be refused at restart time - after the
        # operator has already taken it out of rotation. Catch it at startup.
        configured = [str(c.get("name")) for c in (whole.get("clusters") or [])
                      if c.get("name")]
        missing = [name for name in configured if name not in inventories]
        if missing:
            raise ConfigError(
                "cluster_ops.ansible.inventories has no entry for {}. TMS will not "
                "guess which hosts to restart.".format(", ".join(sorted(missing))))
        unknown = [name for name in inventories if configured and name not in configured]
        if unknown:
            raise ConfigError(
                "cluster_ops.ansible.inventories names unknown cluster(s): {}".format(
                    ", ".join(sorted(unknown))))

    drain_timeout = float(raw.get("drain_timeout_seconds", 900))
    if drain_timeout <= 0:
        raise ConfigError("cluster_ops.drain_timeout_seconds must be positive")

    return ClusterOpsConfig(restart_mode=mode, drain_timeout_seconds=drain_timeout,
                            ansible=ansible)


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

    cluster_ops = _build_cluster_ops(raw.get("cluster_ops") or {}, raw)
    fleet = _build_fleet(raw.get("fleet") or {})

    resource_groups_raw = raw.get("resource_groups") or {}
    resource_groups = ResourceGroupStoreConfig(
        enabled=bool(resource_groups_raw.get("enabled", False)),
        schema=str(resource_groups_raw.get("schema") or "trino_resource_groups"),
        group_provider_configured=bool(
            resource_groups_raw.get("group_provider_configured", False)),
    )
    if resource_groups.enabled:
        from tms.ops.config_store import valid_schema_name

        if not valid_schema_name(resource_groups.schema):
            raise ConfigError(
                "resource_groups.schema must be a plain SQL identifier, got {!r}"
                .format(resource_groups.schema))
    # A cluster without node_environment is not fatal - the check abstains for
    # it. configcheck reports that as a warning, which is where "this is set up
    # but not doing anything" belongs.

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
        cluster_ops=cluster_ops,
        fleet=fleet,
        resource_groups=resource_groups,
        benchmark=_build_benchmark(raw.get("benchmark") or {}),
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
