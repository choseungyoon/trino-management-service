"""Entry point for tms-collector.

The single process that polls Trino. Two independent guards keep it that way:
the systemd unit is not a template, and the service takes a PostgreSQL advisory
lock at startup. Running two collectors would double the load on every
coordinator and break NFR-PERF-03 quietly, which is the worst way to break it
(ARCHITECTURE.md principle A3).

Failure policy: a coordinator being unreachable is a normal operating condition,
not a reason to exit. Only losing the database - which makes every snapshot
unwritable - stops the service.

Python 3.9 compatible.
"""

import logging
import os
import signal
import sys
import time
from typing import List, Optional

from tms.clients.circuit import CircuitBreaker
from tms.clients.transport import HttpxTransport
from tms.clients.trino import TrinoClient
from tms.collector.health_writer import HealthWriter
from tms.collector.poller import ClusterPoller
from tms.collector.postgres import PostgresSnapshotRepository
from tms.core.config import Config, load_config
from tms.health.engine import HealthEngine

log = logging.getLogger("tms.collector")

DEFAULT_CONFIG_PATH = "/etc/trino-management-service/config/config.yaml"
# Floor on the sleep between ticks so a misconfigured interval cannot spin.
MIN_TICK_SLEEP_SECONDS = 0.2


class CollectorService:
    def __init__(
        self,
        config: Config,
        repository,
        pollers: List[ClusterPoller],
        health_writer=None,
        gateway_poller=None,
        fleet_pollers=None,
    ) -> None:
        self.config = config
        self.repository = repository
        self.pollers = pollers
        # Fleet-level, so it is not one of the per-cluster pollers. None when
        # gateway.enabled is false - then no Gateway request is ever made.
        self.gateway_poller = gateway_poller
        # One per cluster, on their own (much slower) schedule: node membership
        # changes on the timescale of a deployment, not a query.
        self.fleet_pollers = list(fleet_pollers or [])
        self._fleet_due = 0.0
        # Health evaluation runs here rather than in the API because the engine
        # carries state (OOM counters, stabilisation counts) and the collector is
        # the only single-instance process.
        self.health_writer = health_writer
        self._stopping = False

    def _expected_workers(self, cluster_name: str) -> int:
        if self.config is None:
            return 0
        try:
            return self.config.cluster(cluster_name).expected_workers
        except KeyError:
            return 0

    def _evaluate_health(self, cluster_name: str) -> None:
        if self.health_writer is None:
            return
        try:
            overrides = self.repository.load_health_overrides(cluster_name)
        except Exception:  # noqa: BLE001 - overrides are optional
            log.exception("failed to load health overrides for %s", cluster_name)
            overrides = {}
        self.health_writer.evaluate(
            cluster_name=cluster_name,
            expected_workers=self._expected_workers(cluster_name),
            overrides=overrides,
            gateway_backends=self._gateway_backends(),
        )

    def _gateway_backends(self):
        """Backends from the Gateway snapshot, for H-08.

        ⛔ None and [] mean different things here and must not be confused.
        None is "TMS could not read the list" and makes H-08 UNKNOWN; an empty
        list is "the Gateway has no backends", which makes H-08 report the
        cluster as unregistered - a BAD verdict. Returning [] when the read
        merely failed would raise a false alarm about routing being broken.
        """
        if self.gateway_poller is None:
            return None
        from tms.collector.snapshot import GATEWAY_SCOPE, KIND_GATEWAY

        try:
            snapshot = self.repository.load(GATEWAY_SCOPE, KIND_GATEWAY)
        except Exception:  # noqa: BLE001
            log.exception("could not load the gateway snapshot for health")
            return None
        if snapshot is None or snapshot.collection_error:
            return None
        return (snapshot.payload or {}).get("backends")

    def _poll_fleet(self) -> None:
        """Contact every node, at most once per fleet interval.

        Its own clock rather than the main loop's: a fleet poll fans out to
        every node in the cluster, and doing that on the query cadence would
        multiply TMS's request count by the worker count for data that barely
        changes.
        """
        if not self.fleet_pollers or self._stopping:
            return
        now = time.monotonic()
        if now < self._fleet_due:
            return
        interval = min(p.interval for p in self.fleet_pollers)
        self._fleet_due = now + max(interval, 5.0)
        for poller in self.fleet_pollers:
            if self._stopping:
                break
            try:
                poller.tick(node_counts=self._node_counts(poller.cluster))
            except Exception:  # noqa: BLE001 - one cluster must not stop the rest
                log.exception("unhandled error polling the fleet of %s", poller.cluster)

    def _node_counts(self, cluster: str) -> dict:
        """The coordinator's own node counts, from the JMX snapshot the query
        collector already wrote. Read rather than re-fetched: asking twice for
        the same numbers is load TMS does not need to add."""
        from tms.collector.snapshot import KIND_JMX

        try:
            snapshot = self.repository.load(cluster, KIND_JMX)
        except Exception:  # noqa: BLE001
            return {}
        beans = ((snapshot.payload if snapshot else {}) or {}).get("mbeans") or {}
        node_manager = beans.get("trino.node:name=CoordinatorNodeManager") or {}
        return {k: v for k, v in node_manager.items() if k.endswith("NodeCount")}

    def request_stop(self, *_args) -> None:
        log.info("shutdown requested, finishing the current tick")
        self._stopping = True

    def run(self) -> int:
        log.info(
            "collector started for %d cluster(s): %s",
            len(self.pollers),
            ", ".join(p.cluster_name for p in self.pollers),
        )
        while not self._stopping:
            for poller in self.pollers:
                if self._stopping:
                    break
                try:
                    produced = poller.tick()
                    if produced:
                        self._evaluate_health(poller.cluster_name)
                except Exception:  # noqa: BLE001 - one cluster must not stop the rest
                    log.exception("unhandled error polling %s", poller.cluster_name)
            if self.gateway_poller is not None and not self._stopping:
                try:
                    self.gateway_poller.tick()
                except Exception:  # noqa: BLE001 - the Gateway must not stop clusters
                    log.exception("unhandled error polling the gateway")
            self._poll_fleet()
            if self._stopping:
                break
            time.sleep(self._sleep_for())
        log.info("collector stopped")
        return 0

    def _sleep_for(self) -> float:
        if not self.pollers:
            return 1.0
        return max(
            MIN_TICK_SLEEP_SECONDS,
            min(poller.seconds_until_next_due() for poller in self.pollers),
        )


def build_pollers(config: Config, repository) -> List[ClusterPoller]:
    pollers = []
    for cluster in config.clusters:
        transport = HttpxTransport(verify_tls=config.trino.verify_tls)
        client = TrinoClient(
            base_url=cluster.coordinator_url,
            user=config.trino.user,
            password=config.trino.password.reveal(),
            transport=transport,
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
        pollers.append(
            ClusterPoller(
                cluster_name=cluster.name,
                client=client,
                repository=repository,
                query_interval=config.collector.query_poll_interval_seconds,
                jmx_interval=config.collector.jmx_poll_interval_seconds,
                info_interval=config.collector.info_poll_interval_seconds,
                query_text_max_bytes=config.collector.query_text_max_bytes,
                long_running_seconds=config.health.long_running_query_seconds,
                response_backoff_bytes=config.collector.response_backoff_bytes,
                response_backoff_interval=config.collector.response_backoff_interval_seconds,
                # None when disabled - the poller then never issues a single
                # resource group request (DESIGN_R2.md 1-4).
                resource_group_interval=(
                    config.workload.poll_interval_seconds
                    if config.workload.enabled else None
                ),
            )
        )
    return pollers


def build_fleet_pollers(config, repository):
    """One poller per cluster with an inventory. Empty when fleet is off."""
    if not getattr(config, "fleet", None) or not config.fleet.enabled:
        return []
    from tms.clients.transport import HttpxTransport
    from tms.collector.fleet_poller import FleetPoller
    from tms.fleet.inventory import load_fleet

    fleet = load_fleet(config.fleet.inventories)
    pollers = []
    for cluster in config.clusters:
        nodes = fleet.get(cluster.name)
        if nodes is None:
            log.warning("fleet.inventories has no entry for %s", cluster.name)
            continue
        pollers.append(FleetPoller(
            cluster=cluster.name, nodes=nodes, repository=repository,
            url_template=config.fleet.node_url_template,
            transport_factory=lambda: HttpxTransport(
                verify_tls=config.trino.verify_tls),
            interval=config.fleet.poll_interval_seconds,
            verify_tls=config.trino.verify_tls,
        ))
    return pollers


def build_gateway_poller(config, repository):
    """None when the Gateway is disabled - then no request is ever made."""
    from tms.clients.gateway import build_gateway_client
    from tms.collector.gateway_poller import GatewayPoller

    client = build_gateway_client(config)
    if client is None:
        return None
    return GatewayPoller(client, repository, clusters=config.clusters,
                         interval=config.gateway.poll_interval_seconds)


def run(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(
        level=os.environ.get("TMS_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config_path = os.environ.get("TMS_CONFIG", DEFAULT_CONFIG_PATH)
    try:
        config = load_config(config_path)
    except Exception as exc:  # noqa: BLE001 - a bad config is a startup failure
        log.error("failed to load configuration from %s: %s", config_path, exc)
        return 2

    try:
        repository = PostgresSnapshotRepository(config.database_url.reveal())
    except Exception as exc:  # noqa: BLE001
        log.error("failed to connect to the TMS database: %s", exc)
        return 3

    if not repository.acquire_singleton_lock():
        # Exit rather than compete: a second collector doubles coordinator load.
        log.error(
            "another tms-collector already holds the advisory lock; exiting. "
            "Only one collector may run - see ARCHITECTURE.md principle A3."
        )
        repository.close()
        return 4

    health_writer = HealthWriter(
        engine=HealthEngine(
            stabilization_polls=config.health.stabilization_polls,
            gateway_enabled=config.gateway.enabled,
        ),
        repository=repository,
        stale_threshold_seconds=config.collector.stale_threshold_seconds,
        thresholds=config.health.thresholds,
        coordinator_counted_in_active_nodes=(
            config.trino_facts.coordinator_counted_in_active_nodes
        ),
    )
    service = CollectorService(
        config, repository, build_pollers(config, repository),
        health_writer=health_writer,
        gateway_poller=build_gateway_poller(config, repository),
        fleet_pollers=build_fleet_pollers(config, repository),
    )
    signal.signal(signal.SIGTERM, service.request_stop)
    signal.signal(signal.SIGINT, service.request_stop)
    try:
        return service.run()
    finally:
        repository.close()


if __name__ == "__main__":
    sys.exit(run())
