"""Which inventory node failed to join discovery (FR-FL-02, D-012).

Until 2026-08-21 TMS could only say *how many* nodes were missing. The
coordinator's `ActiveNodeCount` MBean gives a count and no identifiers, and
`GET /v1/node` does not exist in Trino 477 - so a fleet screen whose whole
purpose is the node inventory could not name the node that was not there.

`system.runtime.nodes` has the identifiers, and D-012 granted the permission to
read it. What matters is *when*:

⛔ **On demand, never on a timer.** This is the exception A1 was narrowed to
allow, and the narrowing only holds while the query count stays near zero. The
collector must not call this - `tests/test_sql_isolation.py` enforces that -
and the screen only offers it when the counts already disagree, which is rare
and is also the only moment the answer is worth a query.

Python 3.9 compatible.
"""

import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

#: Only five columns exist (`NodeSystemTable.java` @477): node_id, http_uri,
#: node_version, coordinator, state. `http_uri` is the only one that can be
#: matched back to an inventory entry.
NODES_QUERY = "SELECT node_id, http_uri, node_version, coordinator, state FROM system.runtime.nodes"


def host_of(uri: str) -> str:
    """'https://10.0.0.11:8443' -> '10.0.0.11'.

    Both sides of the comparison get the same treatment, so a scheme or port
    that differs between the inventory and Trino's own view cannot masquerade
    as a missing node.
    """
    from urllib.parse import urlsplit

    if not uri:
        return ""
    text = str(uri).strip()
    if "://" not in text:
        text = "//" + text
    return (urlsplit(text).hostname or "").lower()


def compare(inventory: List[Dict[str, Any]], joined: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Line the inventory up against what the coordinator can actually see.

    Two directions, and both are worth reporting:

    * inventory but not joined - the case FR-FL-02 asks for. The node may be
      running perfectly and still be invisible to the coordinator, which is why
      "it answers /v1/info" was never enough to conclude it had joined.
    * joined but not in the inventory - a node the platform team does not know
      about is serving queries. Rarer and more alarming.
    """
    joined_hosts = {}
    for row in joined or []:
        host = host_of(row.get("http_uri"))
        if host:
            joined_hosts[host] = row

    unjoined = []
    matched = set()
    for node in inventory or []:
        candidates = {host_of(node.get("address")), host_of(node.get("host"))} - {""}
        hit = next((h for h in candidates if h in joined_hosts), None)
        if hit is None:
            unjoined.append(node)
        else:
            matched.add(hit)

    unexpected = [row for host, row in sorted(joined_hosts.items())
                  if host not in matched]

    return {
        "unjoined": unjoined,
        "unexpected": unexpected,
        "joined_count": len(joined_hosts),
        "inventory_count": len(inventory or []),
    }


def identify(sql_client, inventory: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Run the query and compare. Never raises.

    A failure here is an answer TMS could not get, not a reason to break the
    screen someone opened during an incident - so it comes back as a message
    rather than an exception.
    """
    from tms.clients.errors import TrinoClientError

    try:
        rows = sql_client.query(NODES_QUERY)
    except TrinoClientError as exc:
        log.warning("could not read system.runtime.nodes: %s", exc)
        return {"available": False, "error": str(exc),
                "advice": _advice_for(exc),
                "unjoined": [], "unexpected": []}

    result = compare(inventory, rows)
    result["available"] = True
    result["error"] = None
    return result


def _advice_for(exc) -> Optional[str]:
    """The one failure worth naming, because its fix is a config change."""
    text = str(exc)
    if "PERMISSION_DENIED" in text or "Cannot execute query" in text:
        return ("The TMS account cannot execute queries. D-012 grants this "
                "deliberately - add `execute` to the `tms-svc` entry in OPA's "
                "`queries` rules, above the catch-all.")
    return None
