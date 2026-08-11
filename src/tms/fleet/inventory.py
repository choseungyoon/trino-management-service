"""Who the nodes are (FR-FL-01, static half).

The requirement names the Ansible inventory as the source of static node facts,
and the platform team already keeps one file per cluster holding that cluster's
coordinator and workers. So TMS reads those files rather than asking anyone to
maintain a second list - a second list is a list that goes stale.

⛔ Read-only, and never executed. An inventory file is data here: this parses
the INI subset Ansible uses for plain host lists and ignores everything it does
not understand. It does not resolve `group_vars`, does not expand patterns, and
does not run Ansible to find out what a file means.

What this deliberately does not provide
---------------------------------------
The requirement's static field list also names golden image version, VM spec
and provisioning date. A plain host-list inventory does not carry them, and
inventing a parse for fields that may not exist would produce confident blanks.
They are reported as absent until there is a source for them.

Python 3.9 compatible.
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

ROLE_COORDINATOR = "coordinator"
ROLE_WORKER = "worker"

# Section names that mean each role. Matched case-insensitively, and the common
# plural/singular spellings are accepted because both are in the wild.
_COORDINATOR_SECTIONS = ("coordinator", "coordinators")
_WORKER_SECTIONS = ("worker", "workers")

_SECTION = re.compile(r"^\[\s*([^\]:]+?)\s*(?::\s*(\w+)\s*)?\]$")
# `host ansible_host=10.0.0.1 http_port=8081` - the leading token is the host.
_HOST = re.compile(r"^([A-Za-z0-9_.:\-]+)")


class Node:
    """One node, as the inventory knows it. No runtime facts here."""

    __slots__ = ("host", "role", "cluster", "variables")

    def __init__(self, host: str, role: str, cluster: str,
                 variables: Optional[Dict[str, str]] = None) -> None:
        self.host = host
        self.role = role
        self.cluster = cluster
        self.variables = dict(variables or {})

    @property
    def address(self) -> str:
        """What to actually connect to.

        `ansible_host` wins when present: the inventory alias is frequently a
        name only Ansible resolves, and connecting to the alias would fail for
        reasons that look like the node being down.
        """
        return self.variables.get("ansible_host") or self.host

    def as_dict(self) -> Dict[str, object]:
        return {"host": self.host, "address": self.address, "role": self.role,
                "cluster": self.cluster}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Node({!r}, {!r}, {!r})".format(self.host, self.role, self.cluster)


#: (group name, role), in the order nodes are listed.
_ROLE_GROUPS = tuple(
    [(name, ROLE_COORDINATOR) for name in _COORDINATOR_SECTIONS]
    + [(name, ROLE_WORKER) for name in _WORKER_SECTIONS]
)


def parse_inventory(text: str, cluster: str) -> List[Node]:
    """Nodes from one inventory file's contents.

    Nodes come from `[coordinator]` and `[worker]` (and their plurals), plus
    anything those groups pull in through `[coordinator:children]` /
    `[workers:children]`. Group names are matched case-insensitively.

    Anything else - `[all:vars]`, `[gateway]`, a group TMS has never heard of -
    is skipped rather than guessed at, because a node with the wrong role
    attached is worse than a node that is missing: `shutdown` treats the two
    roles very differently. `:children` does not weaken that: a group is only
    read if one of the two role groups reaches it.
    """
    # Every group's hosts, plus every `[x:children]` membership. Both are
    # collected in one pass so that a group can be defined after the group that
    # includes it - Ansible does not require an order and neither does this.
    hosts: Dict[str, List[Dict[str, Any]]] = {}
    children: Dict[str, List[str]] = {}
    group = None
    kind = None

    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].split(";", 1)[0].strip()
        if not line:
            continue

        section = _SECTION.match(line)
        if section:
            group = section.group(1).strip().lower()
            kind = (section.group(2) or "hosts").lower()
            continue
        if group is None:
            continue

        if kind == "children":
            children.setdefault(group, []).append(line.strip().lower())
            continue
        if kind != "hosts":
            continue          # `[x:vars]` is a variables block, not hosts

        match = _HOST.match(line)
        if not match:
            continue
        variables = {}
        for token in line[match.end():].split():
            if "=" in token:
                key, _, value = token.partition("=")
                variables[key.strip()] = value.strip()
        hosts.setdefault(group, []).append(
            {"host": match.group(1), "variables": variables})

    def collect(name: str, seen_groups: set) -> List[Dict[str, Any]]:
        """Hosts of a group, following `:children` the way Ansible does.

        Supported because it is the standard way to alias an existing group,
        and aliasing beats editing: a team whose inventory already says
        `[trino_coordinator]` can add four lines rather than rename a group
        their playbooks depend on.
        """
        if name in seen_groups:
            return []          # inventories do contain accidental cycles
        seen_groups.add(name)
        found = list(hosts.get(name, []))
        for child in children.get(name, []):
            found += collect(child, seen_groups)
        return found

    nodes: List[Node] = []
    seen = set()
    for group_name, role in _ROLE_GROUPS:
        for entry in collect(group_name, set()):
            key = (entry["host"], role)
            if key in seen:
                continue
            seen.add(key)
            nodes.append(Node(host=entry["host"], role=role, cluster=cluster,
                              variables=entry["variables"]))
    return nodes


def load_inventory(path: str, cluster: str) -> List[Node]:
    """Nodes from one inventory file. Missing or unreadable yields none.

    Never raises: the fleet screen degrades to "TMS cannot see the inventory"
    and says so. Taking the console down because one file moved would remove
    the screen an operator is using to work out what moved.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            return parse_inventory(handle.read(), cluster)
    except OSError as exc:
        log.warning("cannot read inventory for %s (%s): %s", cluster, path, exc)
        return []


def load_fleet(inventories: Dict[str, str]) -> Dict[str, List[Node]]:
    """{cluster: nodes} for every configured inventory."""
    fleet: Dict[str, List[Node]] = {}
    for cluster, path in sorted((inventories or {}).items()):
        if not path or not os.path.isabs(path):
            log.warning("inventory path for %s must be absolute: %r", cluster, path)
            fleet[cluster] = []
            continue
        fleet[cluster] = load_inventory(path, cluster)
    return fleet
