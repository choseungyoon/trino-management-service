"""The thing that notices a schedule is due.

A daemon thread in the API process, because that is where benchmark execution
already lives: the runner, the audit guard and the read-only allowlist are all
here, and putting the timer in `tms-collector` would need a second copy of all
three plus a way to stop the two processes starting the same run.

⛔ That does *not* make correctness depend on there being one API process.
`claim_due` moves the row forward in the same UPDATE that selects it, so two
processes ticking the same second cannot both take a schedule. The single host
is a fact about the deployment, not an assumption in the code.

⛔ Nothing here decides anything. It wakes up, calls `tick_schedules`, and goes
back to sleep. Whether a run may start, what its reason is, and when a broken
schedule gets switched off are all decided in the service - where they are
testable without a clock.

Python 3.9 compatible.
"""

import logging
import threading

log = logging.getLogger(__name__)

#: How often to look. Not how often anything runs - the shortest interval a
#: schedule may have is measured in tens of minutes, so this only bounds how
#: late a run can be.
TICK_SECONDS = 30.0


class ScheduleTicker:
    def __init__(self, benchmark, interval: float = TICK_SECONDS) -> None:
        self.benchmark = benchmark
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        """Begin ticking, or do nothing if there is nothing to tick."""
        if self.benchmark is None or getattr(self.benchmark, "schedules", None) is None:
            log.info("benchmark schedules are off; no scheduler thread started")
            return None
        self._thread = threading.Thread(target=self._loop, name="benchmark-schedules",
                                        daemon=True)
        self._thread.start()
        log.info("benchmark schedule ticker started (every %.0fs)", self.interval)
        return self._thread

    def stop(self):
        self._stop.set()

    def _loop(self):
        # ⛔ Wait first. A process that restarts in a loop would otherwise fire
        # every due schedule on each restart, which is the one failure mode
        # that turns a scheduler into a load generator.
        while not self._stop.wait(self.interval):
            try:
                for outcome in self.benchmark.tick_schedules():
                    if outcome["started"]:
                        log.info("schedule %s started on %s", outcome["schedule"],
                                 ", ".join(outcome["started"]))
            except Exception:  # noqa: BLE001 - a tick reports, it never dies
                log.exception("the benchmark schedule tick failed")
