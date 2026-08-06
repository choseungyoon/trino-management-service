#!/usr/bin/env python3
"""NFR-PERF-03: measure the CPU the TMS collector adds to a Trino coordinator.

Runs the real ClusterPoller at production intervals and measures the
coordinator process's CPU delta, alternating idle and polling windows.

Two methodology points that materially change the answer:

* The idle baseline must be subtracted. Trino burns CPU on announcer,
  heartbeat and GC with no requests at all - measured at ~1.6% of one core on
  an idle single node. Without subtracting it, polling looks several times more
  expensive than it is.
* Per-endpoint costs are extrapolated badly. The same MBean measured 3.27 and
  1.52 CPU ms/req in consecutive runs. Running the actual collector and
  measuring the delta avoids multiplying noisy per-request figures.

Read docs/PERF_MEASUREMENT.md for the limits before trusting a number from
this - a local single-node run understates production in at least three ways.

Usage:
    python3 scripts/measure_coordinator_load.py \\
        --coordinator https://127.0.0.1:8443 --user tms-svc --insecure

Password comes from TMS_TRINO_PASSWORD. Requires httpx (the collector's own
dependency). Python 3.9 compatible.
"""

import argparse
import os
import statistics
import subprocess
import sys
import time

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
)

from tms.clients.transport import HttpxTransport  # noqa: E402
from tms.clients.trino import TrinoClient  # noqa: E402
from tms.collector.poller import ClusterPoller  # noqa: E402
from tms.collector.snapshot import InMemorySnapshotRepository  # noqa: E402

TRINO_PROCESS_PATTERN = "io.trino.server.TrinoServer"


def find_coordinator_pid(pattern):
    result = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
    pids = result.stdout.split()
    if not pids:
        raise SystemExit(
            "coordinator process not found (pattern: {}). This script must run on "
            "the coordinator host.".format(pattern)
        )
    return pids[0]


def cpu_seconds(pid):
    """Cumulative process CPU time in seconds, parsed from ps."""
    out = subprocess.run(
        ["ps", "-o", "cputime=", "-p", pid], capture_output=True, text=True
    ).stdout.strip()
    if not out:
        raise SystemExit("process {} disappeared".format(pid))
    total = 0.0
    for part in out.replace("-", ":").split(":"):
        total = total * 60 + float(part)
    return total


def run_window(pid, seconds, poller):
    """Measure one window. `poller` of None means an idle baseline window."""
    start_cpu = cpu_seconds(pid)
    start = time.time()
    polls = 0
    while time.time() - start < seconds:
        if poller is None:
            time.sleep(1.0)
            continue
        polls += len(poller.tick())
        time.sleep(max(0.2, min(1.0, poller.seconds_until_next_due())))
    wall = time.time() - start
    cpu = cpu_seconds(pid) - start_cpu
    return cpu / wall, polls, cpu, wall


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coordinator", required=True)
    parser.add_argument("--user", default="tms-svc")
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--window", type=int, default=90, help="seconds per window")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--query-interval", type=float, default=5.0)
    parser.add_argument("--jmx-interval", type=float, default=15.0)
    parser.add_argument("--info-interval", type=float, default=30.0)
    parser.add_argument("--process-pattern", default=TRINO_PROCESS_PATTERN)
    args = parser.parse_args()

    password = os.environ.get("TMS_TRINO_PASSWORD")
    if not password:
        print("ERROR: set TMS_TRINO_PASSWORD first", file=sys.stderr)
        return 2

    pid = find_coordinator_pid(args.process_pattern)
    print("=" * 68)
    print("NFR-PERF-03 measurement | coordinator pid {}".format(pid))
    print("intervals: query {}s / jmx {}s / info {}s".format(
        args.query_interval, args.jmx_interval, args.info_interval))
    print("windows: {}s x {} rounds, alternating".format(args.window, args.rounds))
    print("=" * 68)

    def build_poller():
        client = TrinoClient(
            base_url=args.coordinator,
            user=args.user,
            password=password,
            transport=HttpxTransport(verify_tls=not args.insecure),
            verify_tls=not args.insecure,
        )
        return ClusterPoller(
            "measured",
            client,
            InMemorySnapshotRepository(),
            query_interval=args.query_interval,
            jmx_interval=args.jmx_interval,
            info_interval=args.info_interval,
        )

    idle_rates, active_rates = [], []
    for round_index in range(args.rounds):
        rate, _, cpu, wall = run_window(pid, args.window, None)
        idle_rates.append(rate)
        print("  idle    #{}  CPU {:.3f}s / {:.1f}s = {:.3f}% of one core".format(
            round_index + 1, cpu, wall, 100 * rate))

        rate, polls, cpu, wall = run_window(pid, args.window, build_poller())
        active_rates.append(rate)
        print("  polling #{}  CPU {:.3f}s / {:.1f}s = {:.3f}% of one core  ({} polls)".format(
            round_index + 1, cpu, wall, 100 * rate, polls))

    idle = statistics.median(idle_rates)
    active = statistics.median(active_rates)
    delta = active - idle

    print("\nidle median      : {:.3f}% of one core".format(100 * idle))
    print("polling median   : {:.3f}% of one core".format(100 * active))
    print("-" * 40)
    print("TMS increment    : {:.3f} percentage points".format(100 * delta))
    print("NFR-PERF-03 (<1%): {}".format("MET" if 100 * delta < 1.0 else "NOT MET"))
    print(
        "\nNote: an idle coordinator already burns {:.1f}% of one core, so the "
        "requirement only makes sense read as an increment.".format(100 * idle)
    )
    print("Limits that make this an UNDER-estimate: see docs/PERF_MEASUREMENT.md section 5.")
    return 0 if 100 * delta < 1.0 else 1


if __name__ == "__main__":
    sys.exit(main())
