#!/usr/bin/env python3
"""NFR-PERF-03 in production: what does the TMS collector cost a live coordinator?

Run this from the TMS host. It reads each coordinator's CPU over JMX
(java.lang:type=OperatingSystem / ProcessCpuTime), so it needs no shell access
to the coordinator - only the tms-svc account that TMS already uses.

Why this is not measure_coordinator_load.py
-------------------------------------------
That script starts a second poller and measures it. Pointing it at production
would add a second collector's worth of load to a coordinator that already has
one. Here the *real* collector is stopped and started, and the difference is
the answer.

Why paired, alternating, short windows
--------------------------------------
The signal is small - 0.60%p of one core when measured locally - and user query
load moves far more than that on its own. Two long windows would mostly measure
whichever window happened to be busier.

So: many short windows, ON and OFF alternating, and the order flipped every
pair (ON,OFF then OFF,ON). Alternating cancels slow drift in user traffic;
flipping the order cancels any bias from the stop/start itself. The verdict
comes from the median of the paired differences, and the spread of those
differences is reported so you can see whether the number means anything at
all.

Stopping the collector is safe: TMS is not in the query path (NFR-ISOLATION).
The console shows stale data for the duration and nothing else changes. The
collector is restarted on every exit path, including Ctrl-C and crashes.

Python 3.9 compatible.
"""

import argparse
import os
import signal
import statistics
import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple

OS_MBEAN = "java.lang:type=OperatingSystem"
QUERY_MANAGER_MBEAN = "trino.execution:name=QueryManager"


# --------------------------------------------------------------------- JMX

def read_mbean(client, base: str, auth, name: str) -> Dict[str, object]:
    response = client.get(base + "/v1/jmx/mbean/" + name, auth=auth)
    if response.status_code != 200:
        raise RuntimeError(
            "{}: HTTP {} reading {}".format(base, response.status_code, name)
        )
    payload = response.json()
    return {a["name"]: a.get("value") for a in payload.get("attributes", [])}


class Coordinator:
    def __init__(self, client, base: str, auth):
        self.client = client
        self.base = base
        self.auth = auth
        os_attrs = read_mbean(client, base, auth, OS_MBEAN)
        cores = os_attrs.get("AvailableProcessors")
        if not isinstance(cores, int) or cores <= 0:
            raise RuntimeError("{}: AvailableProcessors unreadable".format(base))
        self.cores = cores

    def cpu_seconds(self) -> float:
        """Cumulative process CPU. ProcessCpuTime is nanoseconds."""
        value = read_mbean(self.client, self.base, self.auth, OS_MBEAN)["ProcessCpuTime"]
        return float(value) / 1e9

    def running_queries(self) -> Optional[int]:
        try:
            value = read_mbean(
                self.client, self.base, self.auth, QUERY_MANAGER_MBEAN
            ).get("RunningQueries")
        except Exception:  # noqa: BLE001 - comparability info, not the measurement
            return None
        return int(value) if isinstance(value, (int, float)) else None


# ----------------------------------------------------------------- collector

class CollectorControl:
    """Stops and starts the collector, and guarantees it comes back."""

    def __init__(self, unit: str, dry_run: bool = False):
        self.unit = unit
        self.dry_run = dry_run
        self.stopped = False

    def _systemctl(self, action: str) -> None:
        if self.dry_run:
            print("      [dry-run] systemctl {} {}".format(action, self.unit))
            return
        subprocess.run(
            ["sudo", "systemctl", action, self.unit],
            check=True,
            capture_output=True,
        )

    def stop(self) -> None:
        self._systemctl("stop")
        self.stopped = True

    def start(self) -> None:
        self._systemctl("start")
        self.stopped = False

    def restore(self) -> None:
        """Idempotent. Called from every exit path."""
        if not self.stopped:
            return
        try:
            self.start()
            print("\n  collector restarted.")
        except Exception as exc:  # noqa: BLE001
            print(
                "\n!! COLLECTOR IS STILL STOPPED and could not be restarted: {}\n"
                "!! Run:  sudo systemctl start {}".format(exc, self.unit),
                file=sys.stderr,
            )


# ---------------------------------------------------------------- measuring

def measure_window(
    coordinators: Dict[str, Coordinator], seconds: int, label: str
) -> Dict[str, Dict[str, float]]:
    starts = {}
    running_before = {}
    for name, coord in coordinators.items():
        running_before[name] = coord.running_queries()
        starts[name] = coord.cpu_seconds()
    t0 = time.time()

    time.sleep(seconds)

    wall = time.time() - t0
    result = {}
    for name, coord in coordinators.items():
        cpu = coord.cpu_seconds() - starts[name]
        running_after = coord.running_queries()
        samples = [q for q in (running_before[name], running_after) if q is not None]
        result[name] = {
            "pct_of_one_core": 100.0 * cpu / wall,
            "cpu_seconds": cpu,
            "wall": wall,
            "running": (sum(samples) / len(samples)) if samples else float("nan"),
        }
        print(
            "      {:<12} {:<10} {:6.2f}% of one core   running~{:.0f}".format(
                label, name, result[name]["pct_of_one_core"], result[name]["running"]
            )
        )
    return result


def run(args) -> int:
    try:
        import httpx
    except ImportError:
        print("httpx is required (it is a TMS dependency)", file=sys.stderr)
        return 2

    password = os.environ.get("TMS_TRINO_PASSWORD")
    if not password:
        print(
            "ERROR: set TMS_TRINO_PASSWORD first, e.g.\n"
            "  read -rs TMS_TRINO_PASSWORD && export TMS_TRINO_PASSWORD",
            file=sys.stderr,
        )
        return 2
    auth = (args.user, password)

    client = httpx.Client(verify=not args.insecure, timeout=15.0)
    coordinators = {}
    for base in args.coordinator:
        base = base.rstrip("/")
        name = base.split("//")[-1]
        try:
            coordinators[name] = Coordinator(client, base, auth)
        except Exception as exc:  # noqa: BLE001
            print("cannot read {}: {}".format(base, exc), file=sys.stderr)
            return 3
        print("  {} : {} cores".format(name, coordinators[name].cores))

    total_minutes = args.pairs * 2 * args.window / 60.0
    print(
        "\n  {} pair(s) x 2 x {}s  ->  about {:.0f} minutes total".format(
            args.pairs, args.window, total_minutes
        )
    )
    print("  The collector will be stopped during OFF windows. Queries are unaffected")
    print("  (NFR-ISOLATION) - the console just shows stale data meanwhile.\n")
    if not args.yes:
        answer = input("  proceed? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            return 1

    collector = CollectorControl(args.collector_unit, dry_run=args.dry_run)
    # Restore the collector no matter how we leave: normal exit, error, Ctrl-C,
    # or SIGTERM. A run that dies with the collector stopped leaves TMS blind.
    def _signal_exit(signum, frame):  # noqa: ARG001
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _signal_exit)

    pairs: List[Tuple[Dict, Dict]] = []
    try:
        for index in range(args.pairs):
            # Flip the order every pair so any bias from the stop/start
            # transition itself cancels out across pairs.
            on_first = index % 2 == 0
            print("  pair {}/{}  ({} first)".format(
                index + 1, args.pairs, "ON" if on_first else "OFF"))

            if on_first:
                on = measure_window(coordinators, args.window, "ON ")
                collector.stop()
                off = measure_window(coordinators, args.window, "OFF")
                collector.start()
            else:
                collector.stop()
                off = measure_window(coordinators, args.window, "OFF")
                collector.start()
                on = measure_window(coordinators, args.window, "ON ")
            pairs.append((on, off))
    except KeyboardInterrupt:
        print("\n  interrupted.")
    finally:
        collector.restore()

    if not pairs:
        return 1
    return report(coordinators, pairs, args)


def report(coordinators, pairs, args) -> int:
    print("\n" + "=" * 68)
    print("  NFR-PERF-03 — collector가 코디네이터에 더하는 CPU")
    print("=" * 68)

    worst_exceeded = False
    inconclusive = False
    for name in coordinators:
        diffs = [on[name]["pct_of_one_core"] - off[name]["pct_of_one_core"]
                 for on, off in pairs]
        running_gap = [abs(on[name]["running"] - off[name]["running"])
                       for on, off in pairs]
        median = statistics.median(diffs)
        spread = statistics.pstdev(diffs) if len(diffs) > 1 else 0.0

        print("\n  {}  ({} cores)".format(name, coordinators[name].cores))
        print("    pair별 차이(%p of one core): "
              + ", ".join("{:+.2f}".format(d) for d in diffs))
        print("    중앙값 {:+.2f}%p    편차(1σ) {:.2f}%p".format(median, spread))

        # Comparability: if user load differed between the two windows of a
        # pair, that pair is measuring traffic, not the collector.
        bad = []
        for i, gap in enumerate(running_gap):
            busiest = max(pairs[i][0][name]["running"], pairs[i][1][name]["running"])
            if gap > max(1.0, 0.25 * busiest):
                bad.append(i + 1)
        if bad:
            print("    ⚠️ pair {} 는 ON/OFF 창의 실행 쿼리 수 차이가 커서 신뢰도가 낮다"
                  .format(", ".join(map(str, bad))))

        # Verdict. An untrustworthy number must not produce a pass or a fail -
        # the same rule the health tests follow, where UNKNOWN never renders as
        # GOOD. If the spread swamps the signal we did not measure the
        # collector, we measured the users.
        if spread > abs(median):
            inconclusive = True
            print("    ⚠️ 판정 불가 — 편차({:.2f}%p)가 신호({:+.2f}%p)보다 크다."
                  .format(spread, median))
            print("       collector 부하가 사용자 트래픽 변동에 묻혔다는 뜻이며,")
            print("       '예산 이내'도 '초과'도 아니다. --pairs 를 늘리거나")
            print("       더 한가한 시간대에 재측정하라.")
        elif median > args.budget:
            worst_exceeded = True
            print("    ❌ 예산 {:.2f}%p 초과".format(args.budget))
        else:
            print("    ✅ 예산 {:.2f}%p 이내".format(args.budget))

    print("\n  ⚠️ 이 수치에는 기존 히스토리 프로젝트의 EventListener 부하가 빠져 있다.")
    print("     NFR-PERF-03 은 합산 기준이다 (DECISIONS.md D-001).")
    print("     정확히 하려면 두 시스템을 함께 멈춘 창을 하나 더 두고 재측정하라.")

    if worst_exceeded:
        print("\n  => 판정: 예산 초과. PERF_MEASUREMENT.md §6-2 대응 순서를 적용하라.")
        return 1
    if inconclusive:
        print("\n  => 판정: **불가**. DoD 를 닫지 마라 - 재측정이 필요하다.")
        return 2
    print("\n  => 판정: 충족. PERF_MEASUREMENT.md §0 의 '잠정' 표기를 제거해도 된다.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coordinator", action="append", required=True,
                        help="https://host:port (반복 지정 가능)")
    parser.add_argument("--user", default="tms-svc")
    parser.add_argument("--collector-unit", default="tms-collector")
    parser.add_argument("--window", type=int, default=120,
                        help="창 하나의 길이(초). 기본 120")
    parser.add_argument("--pairs", type=int, default=6,
                        help="ON/OFF 쌍의 수. 기본 6 (총 24분)")
    parser.add_argument("--budget", type=float, default=1.0,
                        help="NFR-PERF-03 예산(%%p of one core). 기본 1.0")
    parser.add_argument("--insecure", action="store_true",
                        help="TLS 검증 생략 (내부 CA 미신뢰 시)")
    parser.add_argument("--dry-run", action="store_true",
                        help="collector를 실제로 멈추지 않는다 (절차 확인용)")
    parser.add_argument("--yes", action="store_true", help="확인 프롬프트 생략")
    return run(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
