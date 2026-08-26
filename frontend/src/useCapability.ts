import { useApi } from "./useApi";

interface Me {
  user: string;
  roles: string[];
  capabilities: string[];
}

/**
 * Does the signed-in operator hold this capability?
 *
 * ⛔ Cosmetic only. Hiding a button the server would refuse saves a wasted
 * click; it is never what stops the write. Every rule lives in the service,
 * and a screen that hid nothing would still be safe.
 *
 * Undefined while `/me` is in flight, so a caller can tell "not yet" from
 * "no" and avoid flashing an editor open and shut.
 */
export function useCapability(name: string): boolean | undefined {
  const { data } = useApi<Me>("/me");
  return data ? data.capabilities.includes(name) : undefined;
}

export const MANAGE_HEALTH = "manage_health";
