import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router";

import { api } from "./api";

import { Icon } from "./components/Icon";
import { applyTheme, currentTheme, type Theme } from "./theme";
import { useApi } from "./useApi";

interface Me {
  user: string;
  roles: string[];
  capabilities: string[];
}

interface ExternalLink {
  id: string;
  label: string;
  url: string;
  icon: string;
}

/**
 * Only screens that exist.
 *
 * The rest of the console is still server-rendered at `/`; listing a link
 * that lands back on Overview would be worse than not listing it. Each entry
 * appears as its screen is ported.
 */
const NAV = [
  { to: "/", label: "Overview", icon: "overview", end: true },
  { to: "/queries", label: "Live Queries", icon: "queries" },
  { to: "/cluster-health", label: "Health", icon: "health" },
  { to: "/workload", label: "Workload", icon: "queries" },
  { to: "/cluster-config", label: "Configuration", icon: "audit" },
  { to: "/resource-groups", label: "Resource Groups", icon: "board" },
  { to: "/fleet", label: "Fleet", icon: "overview" },
  { to: "/restart", label: "Safe Restart", icon: "history" },
  { to: "/gateway", label: "Gateway", icon: "trino" },
  { to: "/benchmark", label: "Benchmark", icon: "clock" },
  { to: "/work", label: "Work Board", icon: "board" },
  { to: "/audit", label: "Audit Log", icon: "audit" },
];

interface ActiveRestart {
  id: number;
  cluster: string;
  actor: string;
  label: string;
  traffic_stopped: boolean;
}

/**
 * A restart in progress, on every screen.
 *
 * ⛔ A cluster held out of rotation is invisible everywhere else: the clusters
 * that remain are green, so the console looks healthy while traffic is being
 * refused. This follows the operator until the sequence finishes.
 */
function RestartAlerts() {
  const { pathname } = useLocation();
  const { data } = useApi<{ active: ActiveRestart[] }>("/restarts", 10_000);
  // Not on the restart screen itself, where the same thing is the whole page.
  const active = pathname.startsWith("/restart") ? [] : (data?.active ?? []);
  if (!active.length) return null;

  return (
    <div className="restart-alert-bar">
      {active.map((restart) => (
        <div className="restart-alert" role="status" key={restart.id}>
          <Icon name="concerning" size={15} stroke={2} />
          <div>
            <b>{restart.cluster} is being restarted</b> by {restart.actor} —{" "}
            {restart.label}
            {restart.traffic_stopped ? ". It is receiving no queries" : ""}.
          </div>
          <Link className="btn btn--sm" to={`/restarts/${restart.id}`}>Open</Link>
        </div>
      ))}
    </div>
  );
}

export function Shell() {
  const [theme, setTheme] = useState<Theme>(currentTheme);
  const [navOpen, setNavOpen] = useState(false);
  const me = useApi<Me>("/me");
  const links = useApi<{ links: ExternalLink[] }>("/links");

  useEffect(() => applyTheme(theme), [theme]);

  // The session is gone and nothing on this page can recover from it. Hand
  // the browser back to the sign-in the server owns.
  if (me.error?.unauthenticated) {
    window.location.href = "/login";
    return null;
  }

  async function signOut() {
    // The cookie is HttpOnly, so only the server can clear it. A full reload
    // afterwards, not a client route change: nothing in memory should survive
    // a sign-out.
    await api.post("/logout").catch(() => undefined);
    window.location.href = "/login";
  }

  const initials = (me.data?.user ?? "").slice(0, 2).toUpperCase();
  const next: Theme = theme === "dark" ? "light" : "dark";

  return (
    <div className="app">
      <a className="skip" href="#main">
        Skip to content
      </a>

      <aside className={`sidebar${navOpen ? " sidebar--open" : ""}`} id="sidebar">
        <div className="brand">
          <div className="brand__glyph" aria-hidden="true">
            T
          </div>
          <div className="brand__name">TMS</div>
        </div>

        <nav className="nav" aria-label="Main">
          {NAV.map((entry) => (
            <NavLink key={entry.to} to={entry.to} end={entry.end}
                     onClick={() => setNavOpen(false)}>
              <Icon name={entry.icon} />
              {entry.label}
            </NavLink>
          ))}
        </nav>

        {links.data?.links?.length ? (
          <>
            <div className="nav-group">TOOLS</div>
            <nav className="nav nav--external" aria-label="External tools">
              {links.data.links.map((link) => (
                <a key={link.id} href={link.url} target="_blank" rel="noopener noreferrer">
                  <Icon name={link.icon} size={14} />
                  {link.label}
                  <span className="ext-mark">
                    <Icon name="external" size={11} stroke={2} />
                  </span>
                </a>
              ))}
            </nav>
          </>
        ) : null}

        <div className="sidebar__foot">
          <div className="avatar" aria-hidden="true">
            {initials}
          </div>
          <Link className="whoami" to="/account" title="Change your password">
            <div className="whoami__user">{me.data?.user ?? "…"}</div>
            <div className="whoami__role">{me.data?.roles?.join(", ")}</div>
          </Link>
          <button
            className="icon-btn"
            type="button"
            title="Sign out"
            aria-label="Sign out"
            onClick={signOut}
          >
            <Icon name="lock" size={13} />
          </button>
          <button
            className="icon-btn"
            type="button"
            title={`Switch to ${next} theme`}
            aria-label={`Switch to ${next} theme`}
            onClick={() => setTheme(next)}
          >
            <Icon name={theme === "dark" ? "sun" : "moon"} size={13} />
          </button>
        </div>
      </aside>

      <div className="main">
        <RestartAlerts />
        <Outlet context={{ setNavOpen }} />
      </div>
    </div>
  );
}
