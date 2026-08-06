"""Health states and roll-up.

UNKNOWN ranks *worse* than GOOD. Not knowing whether a cluster can run queries
is not the same as knowing it can, and a console that renders missing data as
green teaches operators to distrust it (NFR-DEGRADE, HEALTH_TESTS.md section 2).

Python 3.9 compatible.
"""

from typing import Iterable, Optional

GOOD = "GOOD"
CONCERNING = "CONCERNING"
BAD = "BAD"
UNKNOWN = "UNKNOWN"

ALL_STATES = (GOOD, CONCERNING, BAD, UNKNOWN)

# Ordering used by the roll-up. GOOD < UNKNOWN < CONCERNING < BAD.
_SEVERITY = {GOOD: 0, UNKNOWN: 1, CONCERNING: 2, BAD: 3}


def severity(state: str) -> int:
    return _SEVERITY.get(state, _SEVERITY[UNKNOWN])


def worst(states: Iterable[str]) -> str:
    """Roll-up: the worst state among the enabled tests.

    An empty iterable yields UNKNOWN - if every test is disabled, the honest
    answer is that we do not know, not that everything is fine.
    """
    candidates = list(states)
    if not candidates:
        return UNKNOWN
    return max(candidates, key=severity)


def requires_advice(state: str) -> bool:
    """BAD and CONCERNING must always carry a remedy."""
    return state in (BAD, CONCERNING)


def is_worse(new_state: str, old_state: Optional[str]) -> bool:
    if old_state is None:
        return new_state != GOOD
    return severity(new_state) > severity(old_state)
