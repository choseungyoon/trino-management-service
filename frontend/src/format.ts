/** "4 minutes ago", the same shape the server-rendered console used. */
export function relativeTime(value: string | null): string {
  if (!value) return "never";
  const seconds = Math.max(0, (Date.now() - new Date(value).getTime()) / 1000);
  if (seconds < 10) return "just now";
  if (seconds < 90) return `${Math.round(seconds)}s ago`;
  const minutes = seconds / 60;
  if (minutes < 90) return `${Math.round(minutes)}m ago`;
  const hours = minutes / 60;
  if (hours < 36) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

/**
 * "in 17h", "in 3d" — the mirror of relativeTime.
 *
 * Its own function rather than a sign flip on `relativeTime`: that one clamps
 * at zero, so every future moment came out as "just now". A schedule due
 * tomorrow said it was about to fire.
 */
export function untilTime(value: string | null): string {
  if (!value) return "—";
  const seconds = (new Date(value).getTime() - Date.now()) / 1000;
  if (seconds <= 0) return "due now";
  if (seconds < 90) return `in ${Math.round(seconds)}s`;
  const minutes = seconds / 60;
  if (minutes < 90) return `in ${Math.round(minutes)}m`;
  const hours = minutes / 60;
  if (hours < 36) return `in ${Math.round(hours)}h`;
  return `in ${Math.round(hours / 24)}d`;
}

/** Human elapsed time: 940ms, 21m 34s, 4h 12m. */
export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || ms < 0) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${String(Math.floor(seconds % 60)).padStart(2, "0")}s`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${String(minutes % 60).padStart(2, "0")}m`;
  return `${Math.floor(hours / 24)}d ${String(hours % 24).padStart(2, "0")}h`;
}

/** Binary-prefixed size. Trino reports raw bytes; operators think in GB. */
export function dataSize(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || bytes < 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${unit === 0 ? value : value.toFixed(1)}${units[unit]}`;
}

export function percent(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined ? "—" : `${value.toFixed(digits)}%`;
}

/**
 * `["global", "adhoc"]` → `global.adhoc`.
 *
 * Trino reports a resource group as a path array; every screen shows it as
 * the dotted name an operator types.
 */
export function resourceGroup(path: string[] | string | null): string {
  if (!path) return "—";
  return Array.isArray(path) ? path.join(".") : path;
}

/** Thousands-separated count. An empty cell under a label reads as zero. */
export function integer(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : Math.round(value).toLocaleString();
}
