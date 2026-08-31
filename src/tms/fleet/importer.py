"""One-time move of a hand-edited inventory into the node table (D-019).

Switching `fleet.source` to `tms` empties the console's node list until
something fills it. Discovery fills most of it - but only for nodes that are
currently answering, and the ones that are not are exactly the entries worth
keeping. So the existing files are read once, before the switch, and every host
in them is carried over.

Everything imported is `manual`. It came from a file somebody maintained, not
from the coordinator, and claiming otherwise would make the first scan look
like it confirmed nodes it never saw. The first scan promotes whatever is real.

    tms-import-inventory --config /etc/tms/config.yaml --dry-run

Python 3.9 compatible.
"""

import argparse
import os
import sys
from typing import Any, Dict, List

REASON = "Imported from the inventory file this cluster used before the node list moved into TMS."


def plan(inventories: Dict[str, str], existing: Dict[str, List[str]]) -> Dict[str, Any]:
    """What the import would add, per cluster. No I/O beyond reading the files."""
    from tms.fleet.inventory import load_inventory

    additions, skipped, unreadable = {}, {}, []
    for cluster, path in sorted((inventories or {}).items()):
        if not os.path.isfile(path):
            unreadable.append((cluster, path))
            continue
        known = set(existing.get(cluster) or [])
        found = load_inventory(path, cluster)
        additions[cluster] = [n for n in found if n.host not in known]
        skipped[cluster] = [n.host for n in found if n.host in known]
    return {"add": additions, "skip": skipped, "unreadable": unreadable}


def run(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Import Ansible inventories into the TMS node list.")
    parser.add_argument("--config", default=os.environ.get("TMS_CONFIG"),
                        help="path to config.yaml")
    parser.add_argument("--from", dest="source", action="append", default=[],
                        metavar="CLUSTER=PATH",
                        help="inventory to read; repeatable. Required once "
                             "fleet.source is 'tms', because by then the "
                             "configured paths are the generated ones.")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be added and change nothing")
    args = parser.parse_args(argv)

    from tms.core.config import load_config

    if not args.config:
        print("--config is required (or set TMS_CONFIG)", file=sys.stderr)
        return 2
    config = load_config(args.config)

    # ⛔ Once the switch is made, `fleet.inventories` holds the paths TMS
    # writes. Importing from those would read back what it just wrote and add
    # nothing, silently - so the source has to be named explicitly.
    if getattr(config.fleet, "source", "inventory") == "tms" and not args.source:
        print("fleet.source is already 'tms', so the configured inventories are\n"
              "the ones TMS generates. Name the old files explicitly:\n"
              "  tms-import-inventory --config ... --from prod-a=/etc/tms/prod-a.ini",
              file=sys.stderr)
        return 2

    sources = dict(config.fleet.inventories)
    for entry in args.source:
        cluster, _, path = entry.partition("=")
        if not path:
            print("--from wants CLUSTER=PATH, got {!r}".format(entry), file=sys.stderr)
            return 2
        sources[cluster.strip()] = path.strip()

    from tms.fleet.nodestore import (
        SOURCE_MANUAL,
        DuplicateNode,
        PostgresNodeRepository,
    )

    repository = PostgresNodeRepository(config.database_url.reveal())
    existing = {}
    for cluster in sources:
        existing[cluster] = [row["host"] for row in repository.list(cluster)]

    result = plan(sources, existing)
    for cluster, path in result["unreadable"]:
        print("skipped {}: {} cannot be read".format(cluster, path))

    added = 0
    for cluster, found in sorted(result["add"].items()):
        for node in found:
            print("{} {} {} ({})".format(
                "would add" if args.dry_run else "adding",
                cluster, node.host, node.role))
            if args.dry_run:
                continue
            try:
                repository.add(cluster=cluster, host=node.host,
                               address=node.address, role=node.role,
                               source=SOURCE_MANUAL, actor="tms-import",
                               reason=REASON)
                added += 1
            except DuplicateNode:
                pass
        for host in result["skip"].get(cluster) or []:
            print("already listed: {} {}".format(cluster, host))

    if args.dry_run:
        print("\nDry run. Nothing was written.")
    else:
        print("\n{} node(s) imported. They are marked as hand-entered until a "
              "scan confirms them.".format(added))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
