/**
 * Dark by default, light on request.
 *
 * The server used to hold this in a cookie. It is client state now: it changes
 * nothing the server decides, and a round trip to repaint a screen is a round
 * trip the operator waits for.
 */
const KEY = "tms.theme";
export type Theme = "dark" | "light";

export function currentTheme(): Theme {
  return (localStorage.getItem(KEY) as Theme) ?? "dark";
}

export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem(KEY, theme);
}
