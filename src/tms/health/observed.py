"""The sentence under a health test's name.

Each test's observation has its own shape, so each gets its own phrasing. A
generic `str(value)` would print a raw dict at an operator mid-incident.

⛔ Lives with the test catalog rather than with a screen. Which tests exist,
and what their numbers mean, is server knowledge - and a client that has not
heard of H-03 must not be the thing deciding how H-03 reads.

Returned as segments, not markup: `[{"text": ..., "strong": bool}]`. The
emphasis carries meaning - the numbers are what the eye should land on - but
HTML inside a JSON payload is a habit that ends badly.

Python 3.9 compatible.
"""

from typing import Any, Dict, List

Segment = Dict[str, Any]


def _plain(text: str) -> Segment:
    return {"text": text, "strong": False}


def _strong(text: str) -> Segment:
    return {"text": text, "strong": True}


def _int(value: Any) -> str:
    return "—" if value is None else "{:,}".format(int(value))


def _percent(value: Any, digits: int = 1) -> str:
    return "—" if value is None else "{:.{d}f}%".format(float(value), d=digits)


def observed_segments(test: Dict[str, Any]) -> List[Segment]:
    test_id = test.get("id")
    observed = test.get("observed_value")
    threshold = test.get("threshold")

    if test_id == "H-03" and isinstance(observed, dict):
        parts = [
            _strong("{} of {}".format(_int(observed.get("active_workers")),
                                      _int(observed.get("expected_workers")))),
            _plain(" workers active"),
        ]
        # ⛔ The planned/unplanned split is the whole point of this test. A
        # worker draining on purpose and one that vanished are different
        # facts, and collapsing them loses the only thing worth reading.
        if observed.get("planned_out"):
            parts += [_plain(" · "), _strong(_int(observed["planned_out"])),
                      _plain(" draining (planned)")]
        if observed.get("unplanned_missing"):
            parts += [_plain(" · "), _strong(_int(observed["unplanned_missing"])),
                      _plain(" missing unplanned")]
        return parts

    if test_id == "H-04" and isinstance(observed, (int, float)):
        return [_strong(_percent(observed, 0)),
                _plain(" of coordinator heap · threshold "),
                _strong(_percent(threshold, 0))]

    if test_id == "H-05" and isinstance(observed, (int, float)):
        return [_strong(_percent(observed)), _plain(" of queries failed · last 5m")]

    if test_id == "H-06" and isinstance(observed, (int, float)):
        return [_strong(_int(observed)), _plain(" internal failures · last 5m")]

    if test_id == "H-07" and isinstance(observed, dict):
        delta = observed.get("delta")
        if delta is None:
            return [_plain("baseline recorded · total "),
                    _strong(_int(observed.get("total")))]
        return [_strong(_int(delta)),
                _plain(" new OOM kills since last poll · total "),
                _strong(_int(observed.get("total")))]

    if isinstance(observed, dict):
        # An unrecognised shape still reads as words, never as a Python repr.
        segments: List[Segment] = []
        for key, value in sorted(observed.items()):
            if segments:
                segments.append(_plain(" · "))
            segments += [_plain(str(key) + " "), _strong(_int(value))]
        return segments

    if observed is None:
        return [_plain("no reading")]
    return [_plain(str(observed))]
