"""Editing config.properties and sending it to a cluster (D-018 §3).

The shape is slice 2's, because the risk is the same shape: a change is drafted
here, proved on a development cluster, and only then allowed near production.
What is different is what a change *is* - a set of edits rather than a file -
and that difference is not a style choice. The scan redacts credential-shaped
values, so TMS's copy of a node's config.properties holds `[REDACTED]` where
the real secrets are; writing that copy back would replace a working keystore
password with that literal string on every node at once.

⛔ Nothing here restarts anything. Trino reads config.properties at startup, so
a deploy leaves a changed file and a cluster still running the old values. The
restart belongs to the safe sequence, which stops traffic and drains first - a
deploy that restarted on its own would be a way around that with a different
label on it.

⛔ The typo check refuses rather than skips. An unknown property name stops the
server from booting (TRINO_VERIFIED T1-8-1), and the only list of valid names
comes from the cluster's own startup output via the scan. No scan, or a scan
that produced no names, means TMS cannot tell a typo from a real property - so
it deploys nothing and says why.

Python 3.9 compatible.
"""

import base64
import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

from tms.api.errors import (
    AuditUnavailableError,
    Forbidden,
    InvalidRequest,
    NotFound,
    ReasonRequiredError,
    UpstreamUnavailable,
)
from tms.api.permissions import MANAGE_HEALTH, VIEW_HEALTH, Principal
from tms.collector.snapshot import KIND_CONFIG
from tms.core.audit import (
    ACTION_CONFIG_CHANGE,
    ACTION_CONFIG_DEPLOY,
    TARGET_CLUSTER,
    TARGET_CONFIG_CHANGE,
    AuditGuard,
    AuditUnavailable,
    ReasonRequired,
)
from tms.ops import configedit, configscan
from tms.ops.ansible import ansible_environment
from tms.ops.configeditstore import ConfigStoreUnavailable, utcnow
from tms.ops.process import stream_command

log = logging.getLogger(__name__)

#: How much playbook output is kept per deployment. Enough to hold the failing
#: task and its context; a full run against a large cluster is mostly "ok:".
MAX_LOG_CHARS = 60_000


class ConfigEditService:
    def __init__(self, config, repository, snapshots, audit_guard: AuditGuard,
                 playbook: str, inventories: Dict[str, str],
                 binary: str = "ansible-playbook",
                 timeout_seconds: float = 900.0,
                 state_dir: str = "/var/lib/trino-management-service",
                 development_clusters: Optional[List[str]] = None,
                 runner=None) -> None:
        if not playbook or not os.path.isabs(playbook):
            raise ValueError(
                "cluster_ops.config_deploy.playbook must be an absolute path")
        if runner is None and not os.path.isfile(playbook):
            raise ValueError("config deploy playbook not found: {}".format(playbook))

        self.config = config
        self.repository = repository
        self.snapshots = snapshots
        self.audit = audit_guard
        self.playbook = playbook
        self.inventories = dict(inventories or {})
        self.binary = binary
        self.timeout_seconds = timeout_seconds
        self.state_dir = state_dir
        self.development = list(development_clusters or [])
        self._runner = runner or self._run_subprocess
        self._lock = threading.Lock()
        self._busy: Dict[str, bool] = {}

    # ------------------------------------------------------------- guards

    def _require_view(self, principal: Principal) -> None:
        if not principal.can(VIEW_HEALTH):
            raise Forbidden("You do not have permission to view configuration.")

    def _require_admin(self, principal: Principal) -> None:
        if not principal.can(MANAGE_HEALTH):
            raise Forbidden(
                "Changing or deploying configuration is restricted to "
                "administrators.")

    def _cluster_or_404(self, cluster: str) -> str:
        try:
            self.config.cluster(cluster)
        except KeyError:
            raise NotFound("Unknown cluster: {}".format(cluster))
        inventory = self.inventories.get(cluster)
        if not inventory:
            raise InvalidRequest(
                "No inventory is configured for {}, so TMS does not know which "
                "hosts to write to.".format(cluster))
        return inventory

    def _change_or_404(self, change_id) -> Dict[str, Any]:
        try:
            found = self.repository.get(change_id)
        except ConfigStoreUnavailable as exc:
            raise UpstreamUnavailable(str(exc))
        if found is None:
            raise NotFound("No such change: {}".format(change_id))
        return found

    def _audited(self, principal, action_type, target_kind, target_id, reason,
                 cluster=None):
        try:
            return self.audit.action(
                actor=principal.username, roles=principal.roles,
                action_type=action_type, target_kind=target_kind,
                target_id=str(target_id), target_cluster=cluster,
                reason=reason, actor_ip=principal.ip)
        except ReasonRequired as exc:
            raise ReasonRequiredError(str(exc))
        except AuditUnavailable as exc:
            raise AuditUnavailableError(str(exc))

    # --------------------------------------------------- what the cluster knows

    def _scan_of(self, cluster: str) -> Dict[str, Any]:
        """The last scan of this cluster, or an empty one.

        This is where the typo check gets its material. `valid_names` is the
        intersection across scanned nodes - a name only one node accepts is a
        name a deploy to all of them would break on.
        """
        snapshot = self.snapshots.load(cluster, KIND_CONFIG)
        if snapshot is None:
            return {"scanned": False, "valid_names": [], "nodes": []}
        payload = snapshot.payload or {}
        compared = configscan.compare(
            payload.get("nodes") or [],
            ignore_missing_nodes=cluster in self.development)
        return {"scanned": True,
                "valid_names": compared.get("valid_names") or [],
                "nodes": compared.get("nodes") or []}

    # ------------------------------------------------------------ reading

    def overview(self, principal: Principal, cluster: str) -> Dict[str, Any]:
        self._require_view(principal)
        self.config.cluster(cluster) if any(
            c.name == cluster for c in self.config.clusters) else None
        try:
            changes = self.repository.list()
            deployments = self.repository.recent_deployments(limit=25)
        except ConfigStoreUnavailable as exc:
            raise UpstreamUnavailable(str(exc))

        scan = self._scan_of(cluster)
        clusters = [c.name for c in self.config.clusters]
        return {
            "cluster": cluster,
            "changes": [self._describe(c, cluster, scan, clusters)
                        for c in changes],
            "deployments": deployments,
            "clusters": [{"name": name, "development": name in self.development}
                         for name in clusters],
            "scanned": scan["scanned"],
            "known_property_count": len(scan["valid_names"]),
            "busy": bool(self._busy.get(cluster)),
        }

    def _describe(self, change: Dict[str, Any], cluster: str,
                  scan: Dict[str, Any], clusters: List[str]) -> Dict[str, Any]:
        """One change with the server's verdict on every cluster attached.

        ⛔ The refusal sentence is computed here, not in the browser. Whether a
        change may go somewhere depends on the scan, the development list and
        the proof mark; recomputing that client-side would be a second opinion
        that drifts from the one the deploy endpoint actually applies.
        """
        entries = change.get("entries") or []
        return dict(
            change,
            summary=configedit.summarise(entries),
            unknown_names=configedit.unknown_names(entries, scan["valid_names"]),
            advice=configedit.role_advice(entries, change.get("target_role"),
                                          scan["nodes"]),
            targets=[{
                "cluster": name,
                "development": name in self.development,
                "refusal": configedit.refuse_deploy(
                    change, name,
                    self.development,
                    # Each cluster answers for itself: two clusters can run
                    # different builds and accept different names.
                    *self._gate_material(name, cluster, scan)),
            } for name in clusters],
        )

    def _gate_material(self, name: str, current: str, scan: Dict[str, Any]):
        """(valid_names, scanned) for `name`, reusing the scan already loaded."""
        if name == current:
            return scan["valid_names"], scan["scanned"]
        other = self._scan_of(name)
        return other["valid_names"], other["scanned"]

    # ------------------------------------------------------------ writing

    def create(self, principal: Principal, title: str, target_role: str,
               entries: List[Dict[str, Any]], notes: str,
               reason: str) -> Dict[str, Any]:
        self._require_admin(principal)
        try:
            clean = configedit.validate(title, target_role, entries)
        except configedit.ConfigEditError as exc:
            raise InvalidRequest(str(exc))

        with self._audited(principal, ACTION_CONFIG_CHANGE, TARGET_CONFIG_CHANGE,
                           clean["title"], reason):
            try:
                return self.repository.create(
                    title=clean["title"], target_role=clean["target_role"],
                    entries=clean["entries"], notes=(notes or "").strip() or None,
                    actor=principal.username)
            except ConfigStoreUnavailable as exc:
                raise UpstreamUnavailable(str(exc))

    def update(self, principal: Principal, change_id, title: str,
               target_role: str, entries: List[Dict[str, Any]], notes: str,
               reason: str) -> Dict[str, Any]:
        """Edit a change. ⛔ Editing clears the proof.

        A change that was proved on a development cluster and then edited is a
        different change. Keeping the mark would let somebody prove one thing
        and ship another - the same rule catalogs follow, for the same reason.
        """
        self._require_admin(principal)
        self._change_or_404(change_id)
        try:
            clean = configedit.validate(title, target_role, entries)
        except configedit.ConfigEditError as exc:
            raise InvalidRequest(str(exc))

        with self._audited(principal, ACTION_CONFIG_CHANGE, TARGET_CONFIG_CHANGE,
                           change_id, reason):
            try:
                return self.repository.update(
                    change_id, title=clean["title"],
                    target_role=clean["target_role"], entries=clean["entries"],
                    notes=(notes or "").strip() or None,
                    verified_on=None, verified_at=None,
                    updated_by=principal.username, updated_at=utcnow())
            except ConfigStoreUnavailable as exc:
                raise UpstreamUnavailable(str(exc))

    def delete(self, principal: Principal, change_id, reason: str) -> Dict[str, Any]:
        """Remove a draft. The deployments it produced stay - they are evidence."""
        self._require_admin(principal)
        change = self._change_or_404(change_id)
        with self._audited(principal, ACTION_CONFIG_CHANGE, TARGET_CONFIG_CHANGE,
                           change_id, reason):
            try:
                self.repository.delete(change_id)
            except ConfigStoreUnavailable as exc:
                raise UpstreamUnavailable(str(exc))
        return {"id": change["id"], "deleted": True}

    # ----------------------------------------------------------- deploying

    def is_busy(self, cluster: str) -> bool:
        with self._lock:
            return bool(self._busy.get(cluster))

    def deploy(self, principal: Principal, change_id, cluster: str,
               reason: str) -> Dict[str, Any]:
        """Write the change onto the cluster's nodes. Does not restart them."""
        self._require_admin(principal)
        inventory = self._cluster_or_404(cluster)
        change = self._change_or_404(change_id)

        # ⛔ Validated again here, not only when the row was written. A change
        # can be edited between the two, and this is the check standing between
        # a plaintext credential and every node in the cluster.
        try:
            clean = configedit.validate(change["title"], change["target_role"],
                                        change.get("entries") or [])
        except configedit.ConfigEditError as exc:
            raise InvalidRequest("This change is no longer valid: {}".format(exc))

        scan = self._scan_of(cluster)
        refusal = configedit.refuse_deploy(change, cluster, self.development,
                                           scan["valid_names"], scan["scanned"])
        if refusal:
            raise InvalidRequest(refusal)

        with self._lock:
            if self._busy.get(cluster):
                raise InvalidRequest(
                    "A configuration deployment to {} is already running."
                    .format(cluster))
            self._busy[cluster] = True

        try:
            with self._audited(principal, ACTION_CONFIG_DEPLOY, TARGET_CLUSTER,
                               "{}:{}".format(cluster, change["title"]), reason,
                               cluster=cluster):
                record = self.repository.start_deployment(
                    change_id=change["id"], title=change["title"],
                    cluster=cluster, target_role=clean["target_role"],
                    entries=clean["entries"], reason=reason,
                    actor=principal.username)
        except Exception:
            with self._lock:
                self._busy[cluster] = False
            raise

        command = [
            self.binary, "--inventory", inventory, self.playbook,
            # ⛔ One of three words, never a host name (D-009). The playbook
            # asserts the vocabulary again before it uses it as a host pattern.
            "--extra-vars", "target_role={}".format(clean["target_role"]),
            "--extra-vars", "config_edits={}".format(
                base64.b64encode(json.dumps(clean["entries"]).encode()).decode()),
        ]
        threading.Thread(
            target=self._deploy_now,
            args=(cluster, record["id"], command, change),
            name="config-deploy-{}".format(cluster), daemon=True).start()

        log.info("config change %r deployed to %s by %s", change["title"],
                 cluster, principal.username)
        return dict(record, state="RUNNING")

    def _deploy_now(self, cluster, deployment_id, command, change):
        lines: List[str] = []
        state, detail = "SUCCEEDED", None
        try:
            result = self._runner(command, self.timeout_seconds, lines.append)
            if result.get("rc") != 0:
                state = "FAILED"
                detail = result.get("error") or "ansible-playbook exited {}".format(
                    result.get("rc"))
        except Exception as exc:  # noqa: BLE001 - a deploy reports, it never dies
            log.exception("configuration deployment to %s failed", cluster)
            state, detail = "FAILED", str(exc)

        try:
            self.repository.finish_deployment(
                deployment_id, state, detail, "\n".join(lines)[-MAX_LOG_CHARS:])
            if state == "SUCCEEDED" and cluster in self.development:
                # ⛔ The file changed. That is not the same as "it works" -
                # Trino has not read it yet, and whether it can is what the
                # restart will show. The mark says where it was placed; the
                # screen says a restart is still owed.
                self.repository.update(change["id"], verified_on=cluster,
                                       verified_at=utcnow())
        except Exception:  # noqa: BLE001
            log.exception("could not record the config deployment to %s", cluster)
        finally:
            with self._lock:
                self._busy[cluster] = False

    def _run_subprocess(self, command, timeout, on_line):
        return stream_command(command, timeout, on_line,
                              env=ansible_environment(self.state_dir),
                              cwd=self.state_dir)


def build_config_edit_service(config, snapshots, audit_guard):
    """Assemble it, or None when no config deploy playbook is configured."""
    ops = getattr(config, "cluster_ops", None)
    deploy = getattr(ops, "config_deploy", None)
    if deploy is None or not deploy.enabled:
        return None
    from tms.ops.configeditstore import PostgresConfigChangeRepository

    try:
        return ConfigEditService(
            config=config,
            repository=PostgresConfigChangeRepository(config.database_url.reveal()),
            snapshots=snapshots,
            audit_guard=audit_guard,
            playbook=deploy.playbook,
            inventories=ops.ansible.inventories,
            binary=ops.ansible.binary,
            timeout_seconds=deploy.timeout_seconds,
            state_dir=ops.ansible.state_dir,
            development_clusters=getattr(ops.config_scan, "development_clusters",
                                         []) or [],
        )
    except Exception as exc:  # noqa: BLE001
        log.error("configuration deployment is off: %s", exc)
        return None
