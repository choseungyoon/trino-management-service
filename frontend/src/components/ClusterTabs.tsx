import { useSearchParams } from "react-router";

import type { Envelope } from "../api";
import { useApi } from "../useApi";

/**
 * Which cluster a per-cluster screen is showing.
 *
 * In the query string rather than the path: it survives a refresh and a
 * pasted link, and every screen that needs it reads the same key.
 */
export function useCluster(): [string, (name: string) => void, string[]] {
  const [params, setParams] = useSearchParams();
  const { data } = useApi<Envelope<{ name: string }[]>>("/clusters");
  const names = data?.data?.map((c) => c.name) ?? [];
  const selected = params.get("cluster") ?? names[0] ?? "";

  const select = (name: string) => {
    const copy = new URLSearchParams(params);
    copy.set("cluster", name);
    setParams(copy, { replace: true });
  };
  return [selected, select, names];
}

export function ClusterTabs({ selected, names, onSelect }: {
  selected: string;
  names: string[];
  onSelect: (name: string) => void;
}) {
  if (names.length < 2) return null;
  return (
    <nav className="segmented" aria-label="Cluster">
      {names.map((name) => (
        <a key={name} href={`?cluster=${encodeURIComponent(name)}`}
           aria-current={selected === name ? "page" : undefined}
           onClick={(e) => { e.preventDefault(); onSelect(name); }}>
          {name}
        </a>
      ))}
    </nav>
  );
}
