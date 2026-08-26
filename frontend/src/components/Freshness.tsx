/**
 * How old the reading is, said out loud.
 *
 * ⛔ Never render missing or stale data as current. The server decides
 * staleness and puts it in the envelope; this only shows what it decided.
 */
export function Freshness({ collectedAt, stale }: {
  collectedAt: string | null;
  stale: boolean;
}) {
  if (!collectedAt) {
    return <span className="stale-badge">No reading yet</span>;
  }
  const at = new Date(collectedAt);
  const text = at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit",
                                           second: "2-digit" });
  if (stale) {
    return <span className="stale-badge">Stale · last read {text}</span>;
  }
  return <span className="dim mono">Read {text}</span>;
}
