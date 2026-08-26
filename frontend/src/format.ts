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
