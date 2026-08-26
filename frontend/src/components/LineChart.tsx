import { duration } from "../format";

export interface Point {
  run_id: number;
  at: string;
  median_ms: number;
  repetitions: number;
}

export interface Series {
  cluster: string;
  points: Point[];
}

const WIDTH = 720;
const HEIGHT = 220;
const PAD = { left: 56, right: 12, top: 12, bottom: 28 };

/** Round an axis top up to something a person would have chosen. */
function niceCeiling(value: number): number {
  if (value <= 0) return 1;
  let step = 1;
  while (step * 10 <= value) step *= 10;
  for (const multiple of [1, 2, 2.5, 5, 10]) {
    if (step * multiple >= value) return step * multiple;
  }
  return step * 10;
}

/**
 * A small line chart, drawn as inline SVG.
 *
 * ⛔ One y-axis, always, and it starts at zero. Two measures of different
 * scale get two charts — a dual-axis chart lets whoever drew it decide which
 * line looks higher — and an axis that starts at the lowest sample turns a 3%
 * difference into a cliff.
 *
 * No charting library: this is a handful of points and a straight line. One
 * arrives when zoom or brushing does, and it replaces only what is below —
 * the numbers come from the server already aggregated.
 */
export function LineChart({ series, label }: { series: Series[]; label: string }) {
  const values = series.flatMap((s) => s.points.map((p) => p.median_ms));
  if (!values.length) return null;

  const top = niceCeiling(Math.max(...values) * 1.1);
  const columns = Math.max(...series.map((s) => s.points.length));
  const plotW = WIDTH - PAD.left - PAD.right;
  const plotH = HEIGHT - PAD.top - PAD.bottom;

  const x = (index: number) =>
    columns === 1 ? PAD.left + plotW / 2 : PAD.left + (plotW * index) / (columns - 1);
  const y = (value: number) => PAD.top + plotH * (1 - value / top);

  const ticks = [0, 0.5, 1].map((fraction) => ({
    y: y(top * fraction),
    value: top * fraction,
  }));

  const longest = series.reduce((a, b) => (a.points.length >= b.points.length ? a : b));
  const labelIndexes = Array.from(
    new Set([0, Math.floor(longest.points.length / 2), longest.points.length - 1]))
    .filter((i) => i >= 0 && i < longest.points.length)
    .sort((a, b) => a - b);

  const clock = (iso: string) =>
    new Date(iso).toLocaleString([], { month: "short", day: "2-digit",
                                       hour: "2-digit", minute: "2-digit" });

  return (
    <figure className="chart">
      <svg className="chart__svg" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img"
           aria-label={label} preserveAspectRatio="xMidYMid meet">
        {/* Recessive: the grid orients, it does not compete with the lines. */}
        {ticks.map((tick) => (
          <g key={tick.value}>
            <line className="chart__grid" x1={PAD.left} x2={PAD.left + plotW}
                  y1={tick.y} y2={tick.y} />
            <text className="chart__tick" x={PAD.left - 8} y={tick.y + 4}
                  textAnchor="end">
              {duration(tick.value)}
            </text>
          </g>
        ))}

        {labelIndexes.map((index) => (
          <text className="chart__tick" key={index} x={x(index)} y={HEIGHT - 8}
                textAnchor={index === 0 ? "start"
                          : index === longest.points.length - 1 ? "end" : "middle"}>
            {clock(longest.points[index].at)}
          </text>
        ))}

        {series.map((entry, slot) => {
          const path = entry.points
            .map((p, i) => `${i === 0 ? "M" : "L"}${x(i)},${y(p.median_ms)}`)
            .join(" ");
          return (
            <g key={entry.cluster}>
              <path className={`chart__line chart__series--${slot % 4}`} d={path} />
              {entry.points.map((p, i) => (
                /* A ring in the surface colour, so two lines crossing stay
                   countable. */
                <circle key={p.run_id} className={`chart__dot chart__series--${slot % 4}`}
                        cx={x(i)} cy={y(p.median_ms)} r={4}>
                  <title>
                    {entry.cluster} · {clock(p.at)} — #{p.run_id} ·{" "}
                    {duration(p.median_ms)} of {p.repetitions} repetition
                    {p.repetitions === 1 ? "" : "s"}
                  </title>
                </circle>
              ))}
            </g>
          );
        })}
      </svg>

      {/* ⛔ Identity is never colour alone. */}
      <figcaption className="chart__legend">
        {series.map((entry, slot) => (
          <span className="chart__key" key={entry.cluster}>
            <span className={`chart__swatch chart__series--${slot % 4}`} aria-hidden="true" />
            <span className="mono">{entry.cluster}</span>
          </span>
        ))}
      </figcaption>
    </figure>
  );
}
