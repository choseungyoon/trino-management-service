"""Geometry for the small line charts, computed on the server.

Kept in Python rather than in a template so it can be tested without a
browser.

⛔ One y-axis, always. Two measures of different scale get two charts - a
dual-axis chart lets whoever drew it decide which line looks higher.

Python 3.9 compatible.
"""

from typing import Any, Dict, List, Optional, Sequence

#: Series colours, in fixed order, never cycled. Validated against their own
#: surface for lightness band, chroma floor, colour-vision separation and
#: contrast - re-run that check before adding a fifth. Status hues are absent
#: on purpose: a series wearing one would read as a verdict.
SERIES_LIGHT = ("#C40090", "#2A4FC0", "#0E93B4", "#6E42C4")
SERIES_DARK = ("#ED43AE", "#3D63D6", "#2E9DC2", "#8C63E0")

#: Beyond four, colour alone stops separating them. The fifth series onward
#: reuses the ramp with a dash pattern, so identity survives both a repeat and
#: a colour-blind reader.
DASHES = ("", "6 3", "2 3", "8 3 2 3")

WIDTH = 720
HEIGHT = 220
PAD_LEFT = 56
PAD_RIGHT = 12
PAD_TOP = 12
PAD_BOTTOM = 28


def _nice_ceiling(value: float) -> float:
    """Round an axis top up to something a person would have chosen."""
    if value <= 0:
        return 1.0
    step = 1.0
    while step * 10 <= value:
        step *= 10
    for multiple in (1, 2, 2.5, 5, 10):
        if step * multiple >= value:
            return step * multiple
    return step * 10


def line_chart(series: Sequence[Dict[str, Any]],
               width: int = WIDTH, height: int = HEIGHT) -> Optional[Dict[str, Any]]:
    """Turn `[{"name", "points": [{"x_label", "y"}]}]` into drawable geometry.

    Returns None when there is nothing worth drawing. ⛔ That includes the case
    where every series has a single point: two clusters measured once each
    draws two dots and no line, which is a chart pretending to be a trend. The
    summary numbers underneath say the same thing without the pretence.
    """
    prepared = [s for s in series if len(s.get("points") or []) >= 1]
    if not prepared or max(len(s["points"]) for s in prepared) < 2:
        return None

    values = [p["y"] for s in prepared for p in s["points"] if p.get("y") is not None]
    if not values:
        return None

    # From zero, not from the minimum. A y-axis that starts at the lowest
    # sample turns a 3% difference into a cliff.
    top = _nice_ceiling(max(values) * 1.1)
    columns = max(len(s["points"]) for s in prepared)
    plot_w = width - PAD_LEFT - PAD_RIGHT
    plot_h = height - PAD_TOP - PAD_BOTTOM

    def x_at(index: int) -> float:
        if columns == 1:
            return PAD_LEFT + plot_w / 2.0
        return PAD_LEFT + plot_w * index / float(columns - 1)

    def y_at(value: float) -> float:
        return PAD_TOP + plot_h * (1.0 - (value / top))

    drawn = []
    for slot, entry in enumerate(prepared):
        points = []
        for index, point in enumerate(entry["points"]):
            if point.get("y") is None:
                continue
            points.append({
                "x": round(x_at(index), 2),
                "y": round(y_at(point["y"]), 2),
                "value": point["y"],
                "label": point.get("x_label", ""),
                "note": point.get("note", ""),
            })
        drawn.append({
            "name": entry["name"],
            "slot": slot % len(SERIES_LIGHT),
            "dash": DASHES[(slot // len(SERIES_LIGHT)) % len(DASHES)],
            "points": points,
            "path": " ".join(
                "{}{},{}".format("M" if i == 0 else "L", p["x"], p["y"])
                for i, p in enumerate(points)),
        })

    ticks = []
    for fraction in (0.0, 0.5, 1.0):
        value = top * fraction
        ticks.append({"y": round(y_at(value), 2), "value": value})

    return {
        "width": width,
        "height": height,
        "plot": {"x": PAD_LEFT, "y": PAD_TOP, "w": plot_w, "h": plot_h},
        "series": drawn,
        "ticks": ticks,
        "x_labels": _x_labels(prepared, columns, x_at),
        "top": top,
    }


def _x_labels(prepared: Sequence[Dict[str, Any]], columns: int,
              x_at) -> List[Dict[str, Any]]:
    """First, middle and last only.

    A label under every point is unreadable at ten samples and worse at fifty,
    and the tooltip on each point carries the exact one anyway.
    """
    longest = max(prepared, key=lambda s: len(s["points"]))["points"]
    if not longest:
        return []
    wanted = {0, len(longest) - 1}
    if len(longest) > 2:
        wanted.add(len(longest) // 2)
    return [{"x": round(x_at(i), 2), "text": longest[i].get("x_label", ""),
             "anchor": ("start" if i == 0
                        else "end" if i == len(longest) - 1 else "middle")}
            for i in sorted(wanted)]


def summarise(values: Sequence[float]) -> Dict[str, Any]:
    """Average and median together, on purpose.

    The comparison screen judges on the median, because one warm-up execution
    drags a mean and the median is what survives it. The mean is still worth
    showing beside it: when the two disagree, that gap *is* the finding - it
    says the distribution has a tail, which a single number cannot.
    """
    numbers = sorted(v for v in values if v is not None)
    if not numbers:
        return {"count": 0, "avg": None, "median": None, "min": None, "max": None}
    middle = len(numbers) // 2
    median = (numbers[middle] if len(numbers) % 2
              else (numbers[middle - 1] + numbers[middle]) / 2.0)
    return {
        "count": len(numbers),
        "avg": sum(numbers) / float(len(numbers)),
        "median": median,
        "min": numbers[0],
        "max": numbers[-1],
    }
