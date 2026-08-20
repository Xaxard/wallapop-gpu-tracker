"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import type { LoginState } from "@/lib/actions/auth-state";
import {
  SESSION_COOKIE,
  authEnv,
  passwordMatches,
  sessionCookieOptions,
  signSession,
} from "@/lib/auth/session";


/** Only same-origin paths may be redirected to after login (see proxy.ts). */
function safeNextPath(value: FormDataEntryValue | null): string {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) return "/";
  return value;
}

/**
 * Exchange the shared password for a signed session cookie.
 *
 * The password is compared in constant time (see `passwordMatches`) and neither
 * the submitted value nor the configured one is ever logged or echoed back. The
 * failure message is the same whichever way the check failed, so this endpoint
 * doesn't become an oracle for whether a password is even configured.
 */
export async function login(_previous: LoginState, formData: FormData): Promise<LoginState> {
  const env = authEnv();
  if (env === null) {
    return {
      error: "Authentication is not configured on this deployment.",
    };
  }

  const submitted = formData.get("password");
  if (typeof submitted !== "string" || submitted.length === 0) {
    return { error: "Enter the dashboard password." };
  }

  if (!(await passwordMatches(submitted, env.password))) {
    return { error: "Incorrect password." };
  }

  const jar = await cookies();
  jar.set({
    name: SESSION_COOKIE,
    value: await signSession(env.sessionSecret),
    ...sessionCookieOptions(),
  });

  redirect(safeNextPath(formData.get("next")));
}

/** Drop the session cookie and bounce to the login page. */
export async function logout(): Promise<void> {
  const jar = await cookies();
  jar.delete(SESSION_COOKIE);
  redirect("/login");
}
