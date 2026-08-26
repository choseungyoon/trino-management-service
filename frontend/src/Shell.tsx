import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router";

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
  { to: "/health", label: "Health", icon: "health" },
  { to: "/workload", label: "Workload", icon: "queries" },
  { to: "/gateway", label: "Gateway", icon: "trino" },
  { to: "/audit", label: "Audit Log", icon: "audit" },
];

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
          <div className="whoami">
            <div className="whoami__user">{me.data?.user ?? "…"}</div>
            <div className="whoami__role">{me.data?.roles?.join(", ")}</div>
          </div>
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
        <Outlet context={{ setNavOpen }} />
      </div>
    </div>
  );
}
