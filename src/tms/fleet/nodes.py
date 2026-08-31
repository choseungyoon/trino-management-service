"""The rules around the node list: what a valid entry is, what a scan of the
coordinator changes, and what the inventory file rendered from it looks like.

No I/O. The store does the writing and the service does the audit; what is
decided here is what the answers mean, so it can be tested without a database
and without an Ansible run.

Python 3.9 compatible.
"""

import re
from typing import Any, Dict, List, Sequence

from tms.fleet.discovery import host_of
from tms.fleet.inventory import ROLE_COORDINATOR, ROLE_WORKER
from tms.fleet.nodestore import SOURCE_DISCOVERED, SOURCE_MANUAL

ROLES = (ROLE_COORDINATOR, ROLE_WORKER)

#: What may appear as a host or an address. Deliberately the same character set
#: `inventory._HOST` parses back, so anything accepted here survives a
#: render/parse round trip. It also happens to exclude whitespace, quotes and
#: `=`, which is what keeps a typed value from becoming a second Ansible
#: variable when the file is written.
_HOSTNAME = re.compile(r"^[A-Za-z0-9_.:\-]+$")


class NodeError(Exception):
    """The entry cannot be accepted, with a message meant for a person."""


def clean(value: Any) -> str:
    return str(value or "").strip()


def validate(cluster: str, host: str, address: str, role: str,
             known_clusters: Sequence[str]) -> Dict[str, str]:
    """Check one hand-entered node and return it normalised.

    ⛔ The host is what gets written into a file that Ansible then executes
    against. Everything outside the character set above is refused rather than
    escaped: a value that has to be escaped to be safe is a value somebody will
    eventually forget to escape.
    """
    cluster, host, address = clean(cluster), clean(host), clean(address)
    role = clean(role).lower()

    if known_clusters and cluster not in known_clusters:
        raise NodeError("{!r} is not a configured cluster.".format(cluster))
    if role not in ROLES:
        raise NodeError("role must be 'coordinator' or 'worker'.")
    if not host:
        raise NodeError("A host name or address is required.")
    if not _HOSTNAME.match(host):
        raise NodeError(
            "{!r} is not a usable host name. Letters, digits, dot, dash, colon "
            "and underscore only - this value is written into an inventory "
            "file.".format(host))
    address = address or host
    if not _HOSTNAME.match(address):
        raise NodeError("{!r} is not a usable address.".format(address))
    return {"cluster": cluster, "host": host, "address": address, "role": role}


def from_discovery(rows: Sequence[Dict[str, Any]], cluster: str) -> List[Dict[str, Any]]:
    """`system.runtime.nodes` rows -> node entries.

    `http_uri` is the only column that names a machine, so it is both the host
    and the address. A row whose URI has no host is dropped rather than stored
    under an empty name.
    """
    found = []
    for row in rows or []:
        host = host_of(row.get("http_uri"))
        if not host:
            continue
        found.append({
            "cluster": cluster,
            "host": host,
            "address": host,
            "role": ROLE_COORDINATOR if row.get("coordinator") else ROLE_WORKER,
            "node_id": clean(row.get("node_id")) or None,
            "version": clean(row.get("node_version")) or None,
            "state": clean(row.get("state")) or None,
        })
    return found


def plan_refresh(existing: Sequence[Dict[str, Any]],
                 found: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """What one scan changes: rows to add, rows to touch, and what it left alone.

    ⛔ There is no `remove` key, and that is the design. A node the coordinator
    stopped reporting is either decommissioned or down, and this cannot tell
    which. Dropping it would take a down node out of every later config
    deployment, so it comes back running an older configuration than its
    siblings - the drift the config scan exists to catch.

    A row somebody added by hand becomes `discovered` once the coordinator
    confirms it: the reason it was typed in (it was invisible) has stopped
    being true, and leaving it manual would make it look permanently exceptional.

    ⛔ Matched on the address as well as the name. Discovery only ever learns
    the host part of `http_uri`, usually an IP, while a row imported from an
    inventory is named by whatever alias the file used - so matching on the
    name alone would add every node a second time under its address and double
    the deployment target list.
    """
    by_host: Dict[str, Dict[str, Any]] = {}
    for row in existing or []:
        by_host[row["host"]] = row
        by_host.setdefault(row.get("address") or row["host"], row)
    added, touched = [], []

    for node in found or []:
        current = by_host.get(node["host"])
        if current is None:
            added.append(node)
            continue
        changes = {"source": SOURCE_DISCOVERED, "node_id": node["node_id"],
                   "version": node["version"], "role": node["role"]}
        # Clearing the reason with the source keeps them consistent: the
        # database refuses a manual row without one, and a stale "worker 9 is
        # being rebuilt" on a node that is now answering is worse than nothing.
        if current.get("source") == SOURCE_MANUAL:
            changes["reason"] = None
        # ⛔ Keyed by the row's own name, not the discovered one: an imported
        # row is named by its inventory alias, and renaming it here would
        # rewrite the file Ansible resolves that alias through.
        touched.append(dict(changes, cluster=node["cluster"],
                            host=current["host"],
                            address=current.get("address") or node["address"]))

    matched = {change["host"] for change in touched}
    silent = [dict(row) for row in existing or [] if row["host"] not in matched]
    return {"added": added, "touched": touched, "silent": silent}


def render_inventory(cluster: str, rows: Sequence[Dict[str, Any]]) -> str:
    """The node list as an Ansible inventory file.

    ⛔ This file is a derived artifact, and says so at the top. TMS writes it
    into its own state directory and never into a path the platform team
    maintains - a file with two authors is a file nobody owns.

    Round-trips through `inventory.parse_inventory`: same two group names, same
    `ansible_host=` variable. Both groups are always emitted, empty if need be,
    so a playbook that names `worker` does not fail with "unknown group" on a
    single-node development cluster.
    """
    lines = [
        "# Generated by TMS from the node list. Do not edit - the next change",
        "# in the console overwrites this file. Cluster: {}".format(cluster),
        "",
    ]
    for group, role in ((ROLE_COORDINATOR, ROLE_COORDINATOR),
                        (ROLE_WORKER, ROLE_WORKER)):
        lines.append("[{}]".format(group))
        for row in rows or []:
            if row.get("role") != role:
                continue
            host = row["host"]
            address = row.get("address") or host
            lines.append(host if address == host
                         else "{} ansible_host={}".format(host, address))
        lines.append("")
    return "\n".join(lines)


def describe_all(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The rows as the screen needs them, with the judgement made on the server.

    ⛔ `answering` is the fact the screen exists to show, and it needs no extra
    storage. Every row a scan touches gets that scan's single timestamp, so the
    newest `last_seen_at` in a cluster *is* when the last scan ran - a row
    holding an older one was not in that scan's answer.

    It matters because a node that has stopped answering still receives every
    configuration deployment. That is deliberate (`plan_refresh` never removes),
    and the list is where somebody sees it and decides.
    """
    seen = [row.get("last_seen_at") for row in rows or []
            if row.get("last_seen_at") is not None]
    scanned_at = max(seen) if seen else None
    return [
        dict(row,
             answering=row.get("last_seen_at") is not None
             and row.get("last_seen_at") == scanned_at,
             hand_entered=row.get("source") == SOURCE_MANUAL)
        for row in rows or []
    ]
