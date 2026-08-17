import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { SESSION_COOKIE, authEnv, verifySession } from "@/lib/auth/session";

/**
 * The password gate for the whole dashboard.
 *
 * `proxy.ts` is the Next 16 name for what used to be `middleware.ts` — the file
 * convention was renamed in v16.0.0 and the exported function with it. Anything
 * written as `middleware.ts` here is simply not loaded, which is a silent
 * failure mode for a security control, so this is the current name on purpose.
 *
 * Every page in this app is a `force-dynamic` service-role Supabase read: the
 * owner's full listing history, alert history and learned resale prices. Before
 * this file existed the only thing protecting any of it was nobody guessing the
 * deployment URL.
 */

const LOGIN_PATH = "/login";

export const config = {
  /**
   * Everything except build assets and the favicon. `/api` is deliberately NOT
   * excluded — `/api/alerts/recent` returns the same alert data the pages do,
   * and the usual boilerplate matcher that skips `api` would have left it as an
   * unauthenticated read of the whole feed.
   *
   * Note that Next still runs Proxy for `/_next/data/*` even when a matcher
   * excludes it, specifically so that protecting a page cannot accidentally
   * leave its data route open.
   */
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};

/** Only allow redirecting back to a path on this origin. A `next` parameter is
 *  attacker-controlled, and echoing it into a redirect unchecked is an open
 *  redirect; `//evil.example` is a protocol-relative URL, hence the second
 *  test. */
function safeNextPath(value: string | null): string | null {
  if (!value) return null;
  if (!value.startsWith("/") || value.startsWith("//")) return null;
  return value;
}

export async function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const isLogin = pathname === LOGIN_PATH;
  const env = authEnv();

  // Fail closed. A deployment missing either secret cannot authenticate anyone,
  // so it serves nothing rather than serving everything. The login page stays
  // reachable so the operator sees an explanation instead of a bare 503 on
  // every URL. The response says which variables are unset, never their values.
  if (env === null) {
    if (isLogin) return NextResponse.next();
    return new NextResponse(
      "Dashboard authentication is not configured: DASHBOARD_PASSWORD and " +
        "SESSION_SECRET must both be set. Refusing to serve any data.\n",
      { status: 503, headers: { "content-type": "text/plain; charset=utf-8" } },
    );
  }

  const authenticated = await verifySession(
    request.cookies.get(SESSION_COOKIE)?.value,
    env.sessionSecret,
  );

  if (isLogin) {
    if (!authenticated) return NextResponse.next();
    const target = safeNextPath(request.nextUrl.searchParams.get("next")) ?? "/";
    return NextResponse.redirect(new URL(target, request.url));
  }

  if (authenticated) return NextResponse.next();

  // Anything that isn't a plain navigation gets a status code rather than a
  // redirect. A 307 on a POST replays the body at the login page, which for a
  // Server Action call means an action id that route doesn't recognise and a
  // confusing error; 401 is both honest and what the caller can act on.
  if (request.method !== "GET" && request.method !== "HEAD") {
    return new NextResponse("Unauthorized\n", {
      status: 401,
      headers: { "content-type": "text/plain; charset=utf-8" },
    });
  }
  if (pathname.startsWith("/api/")) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const loginUrl = new URL(LOGIN_PATH, request.url);
  if (pathname !== "/") loginUrl.searchParams.set("next", `${pathname}${search}`);
  return NextResponse.redirect(loginUrl);
}
