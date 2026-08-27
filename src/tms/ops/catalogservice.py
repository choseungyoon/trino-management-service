"""Catalogs: drafts here, files on the nodes (FR-CATALOG, D-018 §2).

⛔ Deploying does not restart. Trino reads static catalogs at startup only
(TRINO_VERIFIED T1-8-4), so a deploy on its own changes nothing that is
running - and that is deliberate. Restarting belongs to the safe restart
sequence, which stops intake, drains and checks health first. A deploy that
restarted by itself would be the path around absolute rule 5.

So a deploy leaves the cluster in a state the screen has to name: the file is
on the nodes, and nothing is using it until somebody runs a restart.

⛔ The development cluster is the validator, not a formality. See
`ops/catalogs.py` for why - it is the only method available (T1-9-3).

Python 3.9 compatible.
"""

import base64
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
from tms.core.audit import (
    ACTION_CATALOG_CHANGE,
    ACTION_CATALOG_DEPLOY,
    TARGET_CATALOG,
    TARGET_CLUSTER,
    AuditGuard,
    AuditUnavailable,
    ReasonRequired,
)
from tms.ops import catalogs
from tms.ops.ansible import ansible_environment
from tms.ops.catalogstore import (
    CatalogStoreUnavailable,
    DuplicateCatalog,
    utcnow,
)
from tms.ops.process import stream_command

log = logging.getLogger(__name__)


class CatalogService:
    def __init__(self, config, repository, audit_guard: AuditGuard,
                 playbook: str, inventories: Dict[str, str],
                 binary: str = "ansible-playbook",
                 timeout_seconds: float = 900.0,
                 state_dir: str = "/var/lib/trino-management-service",
                 development_clusters: Optional[List[str]] = None,
                 runner=None) -> None:
        if not playbook or not os.path.isabs(playbook):
            raise ValueError(
                "cluster_ops.catalog_deploy.playbook must be an absolute path")
        if runner is None and not os.path.isfile(playbook):
            raise ValueError("catalog deploy playbook not found: {}".format(playbook))

        self.config = config
        self.repository = repository
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
            raise Forbidden("You do not have permission to view catalogs.")

    def _require_admin(self, principal: Principal) -> None:
        if not principal.can(MANAGE_HEALTH):
            raise Forbidden(
                "Changing or deploying a catalog is restricted to administrators.")

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

    def _draft_or_404(self, catalog_id) -> Dict[str, Any]:
        try:
            found = self.repository.get(catalog_id)
        except CatalogStoreUnavailable as exc:
            raise UpstreamUnavailable(str(exc))
        if found is None:
            raise NotFound("No such catalog: {}".format(catalog_id))
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

    # ------------------------------------------------------------ reading

    def overview(self, principal: Principal) -> Dict[str, Any]:
        self._require_view(principal)
        try:
            drafts = self.repository.list()
            deployments = self.repository.recent_deployments(limit=25)
        except CatalogStoreUnavailable as exc:
            raise UpstreamUnavailable(str(exc))

        clusters = [c.name for c in self.config.clusters]
        return {
            "catalogs": [self._draft(d, clusters) for d in drafts],
            "deployments": deployments,
            "clusters": [{"name": name, "development": name in self.development}
                         for name in clusters],
            "development_clusters": list(self.development),
            "can_edit": principal.can(MANAGE_HEALTH),
            "busy": {name: self.is_busy(name) for name in clusters},
        }

    def _draft(self, row: Dict[str, Any], clusters: List[str]) -> Dict[str, Any]:
        out = dict(row)
        out["file"] = catalogs.render(row["connector"], row["properties"] or {})
        out["environment"] = catalogs.environment_references(row["properties"] or {})
        out["fingerprint"] = catalogs.fingerprint(row["connector"],
                                                  row["properties"] or {})
        out["targets"] = catalogs.deployable(row, clusters, self.development)
        for column in ("verified_at", "created_at", "updated_at"):
            value = out.get(column)
            out[column] = value.isoformat() if hasattr(value, "isoformat") else value
        return out

    def is_busy(self, cluster: str) -> bool:
        with self._lock:
            return bool(self._busy.get(cluster))

    # ------------------------------------------------------------ drafting

    def create(self, principal: Principal, name: str, connector: str,
               properties: Dict[str, str], reason: Optional[str],
               notes: Optional[str] = None) -> Dict[str, Any]:
        self._require_admin(principal)
        try:
            fields = catalogs.validate(name, connector, properties)
        except catalogs.CatalogError as exc:
            raise InvalidRequest(str(exc))

        with self._audited(principal, ACTION_CATALOG_CHANGE, TARGET_CATALOG,
                           fields["name"], reason):
            try:
                row = self.repository.create(
                    fields["name"], fields["connector"], fields["properties"],
                    notes, principal.username)
            except DuplicateCatalog:
                raise InvalidRequest(
                    "A catalog called {!r} already exists.".format(fields["name"]))
            except CatalogStoreUnavailable as exc:
                raise UpstreamUnavailable(str(exc))
        return self._draft(row, [c.name for c in self.config.clusters])

    def save(self, principal: Principal, catalog_id, connector: str,
             properties: Dict[str, str], reason: Optional[str],
             notes: Optional[str] = None) -> Dict[str, Any]:
        """Edit a draft. ⛔ The name never changes - it is the filename on
        every node that already has it, and a rename would leave the old file
        behind while adding a new one."""
        self._require_admin(principal)
        existing = self._draft_or_404(catalog_id)
        try:
            fields = catalogs.validate(existing["name"], connector, properties)
        except catalogs.CatalogError as exc:
            raise InvalidRequest(str(exc))

        changes: Dict[str, Any] = {
            "connector": fields["connector"], "properties": fields["properties"],
            "notes": notes, "updated_by": principal.username,
            "updated_at": utcnow(),
        }
        # ⛔ An edited draft is a different draft. Without this somebody proves
        # a working catalog on the development cluster, changes a property, and
        # ships the change on the strength of a test that never saw it.
        if catalogs.fingerprint(existing["connector"], existing["properties"]) != \
                catalogs.fingerprint(fields["connector"], fields["properties"]):
            changes.update(verified_on=None, verified_at=None)

        with self._audited(principal, ACTION_CATALOG_CHANGE, TARGET_CATALOG,
                           existing["name"], reason):
            row = self.repository.update(catalog_id, **changes)
        return self._draft(row, [c.name for c in self.config.clusters])

    def delete(self, principal: Principal, catalog_id,
               reason: Optional[str]) -> None:
        """Deletes the draft. ⛔ Not the files - a catalog already on a cluster
        stays there until it is removed from that cluster."""
        self._require_admin(principal)
        existing = self._draft_or_404(catalog_id)
        with self._audited(principal, ACTION_CATALOG_CHANGE, TARGET_CATALOG,
                           existing["name"], reason):
            self.repository.delete(catalog_id)

    # ----------------------------------------------------------- deploying

    def deploy(self, principal: Principal, catalog_id, cluster: str,
               reason: Optional[str], action: str = "deploy") -> Dict[str, Any]:
        """Write the file onto every node of one cluster. Does not restart."""
        self._require_admin(principal)
        inventory = self._cluster_or_404(cluster)
        draft = self._draft_or_404(catalog_id)

        if action not in ("deploy", "remove"):
            raise InvalidRequest("action must be 'deploy' or 'remove'.")

        if action == "deploy":
            # ⛔ Validated again here, not only when the row was written. A
            # draft can be edited between the two, and this is the check that
            # stands between a plaintext password and twenty-four nodes.
            try:
                catalogs.validate(draft["name"], draft["connector"],
                                  draft["properties"] or {})
            except catalogs.CatalogError as exc:
                raise InvalidRequest(
                    "This catalog is no longer valid: {}".format(exc))

            refusal = catalogs.refuse_deploy(draft, cluster, self.development)
            if refusal:
                raise InvalidRequest(refusal)

        with self._lock:
            if self._busy.get(cluster):
                raise InvalidRequest(
                    "A catalog deployment to {} is already running.".format(cluster))
            self._busy[cluster] = True

        try:
            with self._audited(principal, ACTION_CATALOG_DEPLOY, TARGET_CLUSTER,
                               "{}:{}".format(cluster, draft["name"]), reason,
                               cluster=cluster):
                record = self.repository.start_deployment(
                    catalog_id=draft["id"], name=draft["name"], cluster=cluster,
                    action=action, connector=draft["connector"],
                    properties=draft["properties"] or {},
                    reason=reason, actor=principal.username)
        except Exception:
            with self._lock:
                self._busy[cluster] = False
            raise

        content = catalogs.render(draft["connector"], draft["properties"] or {})
        command = [
            self.binary, "--inventory", inventory, self.playbook,
            "--extra-vars", "catalog_name={}".format(draft["name"]),
            "--extra-vars", "catalog_action={}".format(action),
            "--extra-vars", "catalog_content={}".format(
                base64.b64encode(content.encode()).decode()),
        ]
        threading.Thread(
            target=self._deploy_now,
            args=(cluster, record["id"], command, draft, action),
            name="catalog-deploy-{}".format(cluster), daemon=True).start()

        log.info("catalog %s %s on %s started by %s", draft["name"], action,
                 cluster, principal.username)
        return dict(record, state="RUNNING")

    def _deploy_now(self, cluster, deployment_id, command, draft, action):
        lines: List[str] = []
        state, detail = "SUCCEEDED", None
        try:
            result = self._runner(command, self.timeout_seconds, lines.append)
            if result.get("rc") != 0:
                state = "FAILED"
                detail = result.get("error") or "ansible-playbook exited {}".format(
                    result.get("rc"))
        except Exception as exc:  # noqa: BLE001 - a deploy reports, it never dies
            log.exception("catalog deployment to %s failed", cluster)
            state, detail = "FAILED", str(exc)

        try:
            self.repository.finish_deployment(deployment_id, state, detail)
            if state == "SUCCEEDED" and action == "deploy" and \
                    cluster in self.development:
                # ⛔ The file landed. That is *not* the same as "it works" -
                # Trino has not read it yet (T1-8-4), and whether it can is
                # what the restart will show. The mark says where it was
                # placed, and the screen says a restart is still owed.
                self.repository.update(draft["id"], verified_on=cluster,
                                       verified_at=utcnow())
        except Exception:  # noqa: BLE001
            log.exception("could not record the catalog deployment to %s", cluster)
        finally:
            with self._lock:
                self._busy[cluster] = False

    def _run_subprocess(self, command, timeout, on_line):
        return stream_command(command, timeout, on_line,
                              env=ansible_environment(self.state_dir),
                              cwd=self.state_dir)


def build_catalog_service(config, audit_guard):
    """Assemble it, or None when no deploy playbook is configured."""
    ops = getattr(config, "cluster_ops", None)
    deploy = getattr(ops, "catalog_deploy", None)
    if deploy is None or not deploy.enabled:
        return None
    from tms.ops.catalogstore import PostgresCatalogRepository

    try:
        return CatalogService(
            config=config,
            repository=PostgresCatalogRepository(config.database_url.reveal()),
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
        log.error("catalog deployment is off: %s", exc)
        return None
