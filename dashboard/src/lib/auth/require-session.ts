import "server-only";
import { cookies } from "next/headers";
import { SESSION_COOKIE, authEnv, verifySession } from "@/lib/auth/session";

/**
 * Server-side session check, for use *inside* Server Actions and Route
 * Handlers rather than only in `src/proxy.ts`.
 *
 * Proxy gating on its own is not enough. A Server Action is not a route of its
 * own: it is a POST to whichever route it happens to be imported into, with an
 * action id that ships in the client bundle and is callable by anyone who reads
 * it. Next's own Proxy docs spell out the consequence — "a matcher change or a
 * refactor that moves a Server Function to a different route can silently
 * remove Proxy coverage. Always verify authentication and authorization inside
 * each Server Function." Since one of these actions writes to the owner's live
 * bot configuration, that check belongs next to the write, not one layer away
 * from it where a matcher edit can delete it.
 */
export async function hasValidSession(): Promise<boolean> {
  const env = authEnv();
  if (env === null) return false;
  const jar = await cookies();
  return verifySession(jar.get(SESSION_COOKIE)?.value, env.sessionSecret);
}

/** Throw unless the caller holds a valid session. The message is deliberately
 *  free of any detail about *why* the session failed. */
export async function requireSession(): Promise<void> {
  if (!(await hasValidSession())) {
    throw new Error("Unauthorized");
  }
}
