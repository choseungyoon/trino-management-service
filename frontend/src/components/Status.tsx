/**
 * The fixed status vocabulary: GOOD / CONCERNING / BAD / UNKNOWN.
 *
 * ⛔ Never colour alone. The word is always there, because "is red bad here"
 * is a question nobody should be answering mid-incident — and because a
 * colour-blind reader gets nothing from the dot.
 */
const CLASSES: Record<string, string> = {
  GOOD: "good",
  CONCERNING: "concerning",
  BAD: "bad",
  UNKNOWN: "unknown",
};

export function Status({ state, large = false }: { state: string; large?: boolean }) {
  // Anything unrecognised is UNKNOWN, never GOOD. Absence of a reading must
  // not read as absence of problems.
  const known = CLASSES[state] ? state : "UNKNOWN";
  return (
    <span className={`status status--${CLASSES[known]}${large ? " status--lg" : ""}`}>
      <i aria-hidden="true" />
      {known}
    </span>
  );
}
