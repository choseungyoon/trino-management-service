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
from typing import Dict, List, Optional

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


def _role_for(section: str) -> Optional[str]:
    name = section.strip().lower()
    if name in _COORDINATOR_SECTIONS:
        return ROLE_COORDINATOR
    if name in _WORKER_SECTIONS:
        return ROLE_WORKER
    return None


def parse_inventory(text: str, cluster: str) -> List[Node]:
    """Nodes from one inventory file's contents.

    Only `[coordinator]` and `[worker]` sections (and their plurals) are read.
    Anything else - `[all:vars]`, `[gateway]`, a group TMS has never heard of -
    is skipped rather than guessed at, because a node with the wrong role
    attached is worse than a node that is missing: `shutdown` treats the two
    roles very differently.
    """
    nodes: List[Node] = []
    seen = set()
    role = None

    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].split(";", 1)[0].strip()
        if not line:
            continue

        section = _SECTION.match(line)
        if section:
            # `[workers:vars]` is a variables block, not a host list.
            role = None if section.group(2) else _role_for(section.group(1))
            continue

        if role is None:
            continue

        match = _HOST.match(line)
        if not match:
            continue
        host = match.group(1)
        variables = {}
        for token in line[match.end():].split():
            if "=" in token:
                key, _, value = token.partition("=")
                variables[key.strip()] = value.strip()

        key = (host, role)
        if key in seen:
            continue
        seen.add(key)
        nodes.append(Node(host=host, role=role, cluster=cluster, variables=variables))

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
