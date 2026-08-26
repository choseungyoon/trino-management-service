import { relativeTime } from "../format";

/**
 * How old the reading is, said out loud.
 *
 * ⛔ Never render missing or stale data as current. The server decides
 * staleness and puts it in the envelope; this only shows what it decided.
 *
 * The markup is `tms.css`'s: a dot that pulses while the reading is current
 * and stops, amber, when it is not. `stale-badge` - which this used until the
 * stylesheet guard was pointed at the components - was a class nobody had
 * written, so the badge rendered as plain text.
 */
export function Freshness({ collectedAt, stale }: {
  collectedAt: string | null;
  stale: boolean;
}) {
  if (!collectedAt) {
    return (
      <div className="freshness" data-stale="true">
        <span className="freshness__dot" aria-hidden="true" />
        <span>no reading yet</span>
      </div>
    );
  }
  const clock = new Date(collectedAt).toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  return (
    <div className="freshness" data-stale={stale ? "true" : "false"}>
      <span className="freshness__dot" aria-hidden="true" />
      <span title={clock}>
        {stale ? "stale · read " : "updated "}
        {relativeTime(collectedAt)}
      </span>
    </div>
  );
}
