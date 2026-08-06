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
from tms.collector.poller import ClusterPoller
from tms.collector.postgres import PostgresSnapshotRepository
from tms.core.config import Config, load_config

log = logging.getLogger("tms.collector")

DEFAULT_CONFIG_PATH = "/opt/tms/config/config.yaml"
# Floor on the sleep between ticks so a misconfigured interval cannot spin.
MIN_TICK_SLEEP_SECONDS = 0.2


class CollectorService:
    def __init__(self, config: Config, repository, pollers: List[ClusterPoller]) -> None:
        self.config = config
        self.repository = repository
        self.pollers = pollers
        self._stopping = False

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
                    poller.tick()
                except Exception:  # noqa: BLE001 - one cluster must not stop the rest
                    log.exception("unhandled error polling %s", poller.cluster_name)
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
            )
        )
    return pollers


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

    service = CollectorService(config, repository, build_pollers(config, repository))
    signal.signal(signal.SIGTERM, service.request_stop)
    signal.signal(signal.SIGINT, service.request_stop)
    try:
        return service.run()
    finally:
        repository.close()


if __name__ == "__main__":
    sys.exit(run())
