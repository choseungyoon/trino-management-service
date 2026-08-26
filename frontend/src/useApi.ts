import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "./api";

export interface Loaded<T> {
  data: T | null;
  error: ApiError | null;
  /** True only for the first load. A refresh must not blank the screen. */
  loading: boolean;
  reload: () => void;
}

/**
 * Read a path, optionally on a timer.
 *
 * `pollMs` exists because several screens answer "what is happening right
 * now". A refresh replaces the data in place and leaves `loading` false —
 * flipping back to a spinner every few seconds makes a live screen unusable
 * and hides the numbers somebody is reading.
 */
export function useApi<T>(path: string | null, pollMs = 0): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const first = useRef(true);

  const load = useCallback(async () => {
    if (path === null) return;
    try {
      const next = await api.get<T>(path);
      setData(next);
      setError(null);
    } catch (caught) {
      // Keep the last good data alongside the error. A screen that empties
      // itself on a transient failure loses what the operator was reading.
      setError(caught instanceof ApiError ? caught : new ApiError(0, "NETWORK", String(caught)));
    } finally {
      if (first.current) {
        first.current = false;
        setLoading(false);
      }
    }
  }, [path]);

  useEffect(() => {
    void load();
    if (!pollMs) return;
    const timer = setInterval(() => void load(), pollMs);
    return () => clearInterval(timer);
  }, [load, pollMs]);

  return { data, error, loading, reload: load };
}
