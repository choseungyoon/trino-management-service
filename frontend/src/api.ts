/**
 * Talking to the TMS API.
 *
 * Auth is the session cookie the server set: HttpOnly, Secure,
 * SameSite=strict, same origin. `credentials: "same-origin"` is all that
 * takes. There is deliberately no token in localStorage — the server owns
 * the session, and a token the page can read is one an injected script can
 * read too.
 */

/** Every read wraps its payload with how fresh it is. The server decides. */
export interface Envelope<T> {
  collected_at: string | null;
  /** True when the snapshot is older than the collector's threshold. */
  stale: boolean;
  data: T;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** The session is gone. The caller sends the operator to sign in again. */
  get unauthenticated(): boolean {
    return this.status === 401;
  }

  /** A feature is switched off, or its store is unreachable. Not a bug. */
  get unavailable(): boolean {
    return this.status === 503;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });

  if (response.status === 204) return undefined as T;

  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const error = body?.error ?? {};
    throw new ApiError(
      response.status,
      error.code ?? "UNKNOWN",
      // The server writes these for an operator to read. Passing them through
      // beats inventing a friendlier sentence that says less.
      error.message ?? `Request failed (${response.status}).`,
    );
  }
  return body as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body ?? {}) }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body ?? {}) }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};
