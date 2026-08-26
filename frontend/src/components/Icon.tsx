/**
 * The icon set the console already uses, as one component.
 *
 * Inline SVG rather than a font or a sprite sheet: they inherit `currentColor`
 * and there is no second request that can fail on a page whose whole job is
 * to load when other things are broken.
 */
const PATHS: Record<string, string> = {
  overview: "M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z",
  queries: "M13 2 3 14h8l-1 8 10-12h-8z",
  health: "M3 12h4l3 8 4-16 3 8h4",
  clock: "M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
  board: "M4 4h6v16H4zM14 4h6v9h-6z",
  audit: "M6 2h9l5 5v15H6zM15 2v5h5M9 13h7M9 17h7",
  lock: "M6 11h12v10H6zM9 11V7a3 3 0 0 1 6 0v4",
  good: "m4 12 5 5L20 6",
  bad: "M6 6l12 12M18 6 6 18",
  concerning: "M12 8v5M12 17v.5M3 20h18L12 3z",
  external: "M14 4h6v6M20 4 10 14M18 14v6H4V6h6",
  sun: "M12 4V2M12 22v-2M4 12H2M22 12h-2M6 6 4.5 4.5M19.5 19.5 18 18M18 6l1.5-1.5M4.5 19.5 6 18M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
  moon: "M20 14a8 8 0 1 1-10-10 7 7 0 0 0 10 10z",
  trino: "M4 6h16M4 12h16M4 18h10",
  superset: "M12 3 3 8l9 5 9-5zM3 14l9 5 9-5",
  grafana: "M3 17l5-6 4 3 5-7 4 4",
  history: "M12 7v5l4 2M4 12a8 8 0 1 0 3-6M4 5v4h4",
  info: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M12 11v5M12 8h.01",
  unknown: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M9.5 9a2.5 2.5 0 1 1 3.4 2.3c-.8.3-.9 1-.9 1.7M12 16.5h.01",
};

export function Icon({ name, size = 15, stroke = 1.7 }: {
  name: string;
  size?: number;
  stroke?: number;
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={PATHS[name] ?? PATHS.overview} />
    </svg>
  );
}
