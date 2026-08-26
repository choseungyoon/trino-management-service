/**
 * A state, wearing its own word.
 *
 * ⛔ Never colour alone. "Is red bad here" is a question nobody should be
 * answering mid-incident, and a colour-blind reader gets nothing from a dot.
 *
 * The mapping mirrors the server's: health states, Trino's query states, and
 * the outcomes of things TMS runs all land in one visual vocabulary. Anything
 * unrecognised is `unknown` — never `good`, because absence of a reading must
 * not read as absence of problems.
 */
const CLASSES: Record<string, string> = {
  GOOD: "good",
  CONCERNING: "concerning",
  BAD: "bad",
  UNKNOWN: "unknown",
  RUNNING: "running",
  FINISHING: "running",
  QUEUED: "queued",
  WAITING_FOR_RESOURCES: "queued",
  PLANNING: "queued",
  STARTING: "queued",
  DISPATCHING: "queued",
  // FAILED is shared with Trino's query states and means the same in both;
  // SUCCEEDED and ABORTED belong only to things TMS runs.
  SUCCEEDED: "good",
  FAILED: "bad",
  ABORTED: "concerning",
};

export function statusClass(state: string): string {
  return CLASSES[String(state).toUpperCase()] ?? "unknown";
}

export function Status({ state, large = false }: { state: string; large?: boolean }) {
  return (
    <span className={`status status--${statusClass(state)}${large ? " status--lg" : ""}`}>
      <i aria-hidden="true" />
      {state}
    </span>
  );
}
