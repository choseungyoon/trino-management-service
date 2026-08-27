"""Reading each cluster's configuration back (FR-CO-01 · FR-FD-01, D-018 §1).

Runs one read-only playbook, parses what it prints, and stores the answer as a
snapshot. Nothing here writes to a node - `docs/templates/collect-config.yml`
has no task that changes anything, and keeping the collection playbook separate
from the restart one is what lets an operator confirm that by reading one short
file.

⛔ On request, never on a timer. A scan opens an SSH connection to every node in
the cluster; doing that every thirty seconds would make TMS the noisiest thing
on the fleet, for an answer that only changes when somebody changes it.

⛔ The playbook is configuration, not input (D-009). Same rule as the restart
executor: an absolute path from `config.yaml`, and the cluster name only ever
selects an inventory file - it never reaches the command line.

Python 3.9 compatible.
"""

import logging
import os
import threading
from typing import Any, Dict, List, Optional

from tms.api.errors import Forbidden, InvalidRequest, NotFound, UpstreamUnavailable
from tms.api.permissions import MANAGE_HEALTH, VIEW_HEALTH, Principal
from tms.collector.snapshot import KIND_CONFIG, Snapshot, utcnow
from tms.ops import configscan
from tms.ops.ansible import ansible_environment
from tms.ops.process import stream_command

log = logging.getLogger(__name__)


class ConfigScanService:
    def __init__(self, config, snapshots, inventories: Dict[str, str],
                 playbook: str, binary: str = "ansible-playbook",
                 timeout_seconds: float = 600.0,
                 state_dir: str = "/var/lib/trino-management-service",
                 development_clusters: Optional[List[str]] = None,
                 runner=None) -> None:
        if not playbook or not os.path.isabs(playbook):
            raise ValueError(
                "cluster_ops.config_scan.playbook must be an absolute path")
        if runner is None and not os.path.isfile(playbook):
            raise ValueError("config scan playbook not found: {}".format(playbook))

        self.config = config
        self.snapshots = snapshots
        self.inventories = dict(inventories or {})
        self.playbook = playbook
        self.binary = binary
        self.timeout_seconds = timeout_seconds
        self.state_dir = state_dir
        self.development = set(development_clusters or [])
        self._runner = runner or self._run_subprocess
        self._lock = threading.Lock()
        self._running: Dict[str, bool] = {}

    # ------------------------------------------------------------- guards

    def _require_view(self, principal: Principal) -> None:
        if not principal.can(VIEW_HEALTH):
            raise Forbidden("You do not have permission to view configuration.")

    def _require_admin(self, principal: Principal) -> None:
        # ⛔ A scan is a read, but it is a read that opens SSH to every node.
        # That is not something a viewer sets off.
        if not principal.can(MANAGE_HEALTH):
            raise Forbidden(
                "Running a configuration scan is restricted to administrators - "
                "it connects to every node in the cluster.")

    def _inventory_or_400(self, cluster: str) -> str:
        try:
            self.config.cluster(cluster)
        except KeyError:
            raise NotFound("Unknown cluster: {}".format(cluster))
        inventory = self.inventories.get(cluster)
        if not inventory:
            raise InvalidRequest(
                "No inventory is configured for {}, so TMS does not know which "
                "hosts belong to it.".format(cluster))
        return inventory

    # ------------------------------------------------------------ reading

    def get(self, principal: Principal, cluster: str) -> Dict[str, Any]:
        """The last scan, compared. Never scans by itself."""
        self._require_view(principal)
        self.config.cluster(cluster) if cluster in [
            c.name for c in self.config.clusters] else None
        snapshot = self.snapshots.load(cluster, KIND_CONFIG)
        development = cluster in self.development

        if snapshot is None:
            return {
                "cluster": cluster,
                "scanned": False,
                "development": development,
                "can_scan": principal.can(MANAGE_HEALTH),
                "scanning": self.is_scanning(cluster),
                "nodes": [], "findings": [], "agree": True,
                "valid_names": [], "roles": [],
            }

        payload = snapshot.payload or {}
        compared = configscan.compare(payload.get("nodes") or [],
                                      ignore_missing_nodes=development)
        return dict(compared,
                    cluster=cluster,
                    scanned=True,
                    development=development,
                    can_scan=principal.can(MANAGE_HEALTH),
                    scanning=self.is_scanning(cluster),
                    collected_at=snapshot.collected_at.isoformat()
                    if hasattr(snapshot.collected_at, "isoformat")
                    else snapshot.collected_at,
                    error=payload.get("error"),
                    exit_code=payload.get("exit_code"))

    def is_scanning(self, cluster: str) -> bool:
        with self._lock:
            return bool(self._running.get(cluster))

    # ------------------------------------------------------------ scanning

    def scan(self, principal: Principal, cluster: str) -> Dict[str, Any]:
        """Run the playbook now, in the background, and say so.

        Returns immediately: a fleet-wide SSH fan-out takes longer than a
        request should, and the screen polls for the result.
        """
        self._require_admin(principal)
        inventory = self._inventory_or_400(cluster)

        with self._lock:
            if self._running.get(cluster):
                raise InvalidRequest(
                    "A scan of {} is already running.".format(cluster))
            self._running[cluster] = True

        command = [self.binary, "--inventory", inventory, self.playbook]
        thread = threading.Thread(
            target=self._scan_now, args=(cluster, command),
            name="config-scan-{}".format(cluster), daemon=True)
        thread.start()
        log.info("configuration scan of %s started by %s", cluster,
                 principal.username)
        return {"cluster": cluster, "scanning": True}

    def _scan_now(self, cluster: str, command: List[str]) -> None:
        lines: List[str] = []
        error = None
        exit_code = None
        try:
            result = self._runner(command, self.timeout_seconds, lines.append)
            exit_code = result.get("rc")
            if exit_code != 0:
                # ⛔ Kept, not discarded. A partial scan still says something
                # about the hosts that did answer, and throwing it away would
                # leave the screen showing the previous scan as though it were
                # current.
                error = result.get("error") or (
                    "ansible-playbook exited {}".format(exit_code))
        except Exception as exc:  # noqa: BLE001 - a scan reports, it never dies
            log.exception("configuration scan of %s failed", cluster)
            error = str(exc)

        nodes = configscan.parse_scan(lines)
        if not nodes and not error:
            error = ("The playbook ran but printed no TMS-CONFIG-SCAN line. "
                     "Check that the installed playbook is the one in "
                     "docs/templates/collect-config.yml.")
        try:
            self.snapshots.save(Snapshot(
                cluster, KIND_CONFIG, utcnow(),
                payload={"nodes": nodes, "error": error, "exit_code": exit_code}))
        except Exception:  # noqa: BLE001
            log.exception("could not store the configuration scan of %s", cluster)
        finally:
            with self._lock:
                self._running[cluster] = False

    def _run_subprocess(self, command, timeout, on_line):
        """Streaming, watchdog and secret redaction are shared with the restart
        executor and the fleet jobs - three copies of that would drift."""
        return stream_command(command, timeout, on_line,
                              env=ansible_environment(self.state_dir),
                              cwd=self.state_dir)


def build_config_scan_service(config, snapshots):
    """Assemble it, or None when no playbook is configured."""
    ops = getattr(config, "cluster_ops", None)
    scan = getattr(ops, "config_scan", None)
    if scan is None or not scan.enabled:
        return None
    try:
        return ConfigScanService(
            config=config, snapshots=snapshots,
            inventories=ops.ansible.inventories,
            playbook=scan.playbook,
            binary=ops.ansible.binary,
            timeout_seconds=scan.timeout_seconds,
            state_dir=ops.ansible.state_dir,
            development_clusters=scan.development_clusters,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("configuration scanning is off: %s", exc)
        return None
