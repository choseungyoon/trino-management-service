import { duration } from "../format";

export interface Point {
  x: number;
  bucket: string;
  at: string;
  median_ms: number;
  avg_ms: number | null;
  runs: number;
  executions: number;
}

export interface Series {
  cluster: string;
  points: Point[];
  mean_of_points: number | null;
}

export interface Bucket {
  key: string;
  at: string;
  runs: number;
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
 * ⛔ Every series is positioned on the *shared* bucket axis the server built,
 * not on its own index. A cluster measured only on the last day belongs at the
 * end of the axis; drawn from its own zero it would claim to have been
 * measured first.
 *
 * No charting library: this is a handful of points and a straight line. One
 * arrives when zoom or brushing does, and it replaces only what is below —
 * the numbers come from the server already aggregated.
 */
export function LineChart({ series, buckets, label, showMean, slotOf }: {
  series: Series[];
  buckets: Bucket[];
  label: string;
  showMean: boolean;
  /** Colour follows the entity, so a hidden series must not repaint the rest. */
  slotOf: (cluster: string) => number;
}) {
  const values = series.flatMap((s) => s.points.map((p) => p.median_ms));
  if (!values.length || buckets.length === 0) return null;

  const means = showMean
    ? series.map((s) => s.mean_of_points).filter((m): m is number => m !== null)
    : [];
  const top = niceCeiling(Math.max(...values, ...means) * 1.1);
  const plotW = WIDTH - PAD.left - PAD.right;
  const plotH = HEIGHT - PAD.top - PAD.bottom;

  const columns = buckets.length;
  const x = (index: number) =>
    columns === 1 ? PAD.left + plotW / 2 : PAD.left + (plotW * index) / (columns - 1);
  const y = (value: number) => PAD.top + plotH * (1 - value / top);

  const ticks = [0, 0.5, 1].map((fraction) => ({
    y: y(top * fraction),
    value: top * fraction,
  }));

  const labelIndexes = Array.from(
    new Set([0, Math.floor((columns - 1) / 2), columns - 1]))
    .filter((i) => i >= 0 && i < columns)
    .sort((a, b) => a - b);

  const clock = (iso: string) =>
    new Date(iso).toLocaleString([], { month: "short", day: "2-digit",
                                       hour: "2-digit", minute: "2-digit",
                                       hour12: false });

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
                          : index === columns - 1 ? "end" : "middle"}>
            {clock(buckets[index].at)}
          </text>
        ))}

        {/* The mean of each series' plotted points, as a dashed rule. Dashed
            so it never reads as another measurement. */}
        {showMean && series.map((entry) => (
          entry.mean_of_points === null ? null : (
            <line className={`chart__mean chart__series--${slotOf(entry.cluster) % 4}`}
                  key={`mean-${entry.cluster}`}
                  x1={PAD.left} x2={PAD.left + plotW}
                  y1={y(entry.mean_of_points)} y2={y(entry.mean_of_points)}>
              <title>
                {entry.cluster} · average of the points drawn —{" "}
                {duration(entry.mean_of_points)}
              </title>
            </line>
          )
        ))}

        {series.map((entry) => {
          const slot = slotOf(entry.cluster) % 4;
          const path = entry.points
            .map((p, i) => `${i === 0 ? "M" : "L"}${x(p.x)},${y(p.median_ms)}`)
            .join(" ");
          return (
            <g key={entry.cluster}>
              <path className={`chart__line chart__series--${slot}`} d={path} />
              {entry.points.map((p) => (
                /* A ring in the surface colour, so two lines crossing stay
                   countable. */
                <circle key={p.bucket} className={`chart__dot chart__series--${slot}`}
                        cx={x(p.x)} cy={y(p.median_ms)} r={4}>
                  <title>
                    {entry.cluster} · {clock(p.at)}{"\n"}
                    median {duration(p.median_ms)} · average {duration(p.avg_ms)}
                    {"\n"}
                    {p.runs} run{p.runs === 1 ? "" : "s"} ·{" "}
                    {p.executions} execution{p.executions === 1 ? "" : "s"}
                  </title>
                </circle>
              ))}
            </g>
          );
        })}
      </svg>
    </figure>
  );
}
