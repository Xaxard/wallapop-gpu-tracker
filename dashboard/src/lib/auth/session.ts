/**
 * Signing and verification for the dashboard's single-password session cookie.
 *
 * Deliberately Web Crypto (`crypto.subtle`), not `node:crypto`. This module is
 * imported by `src/proxy.ts`, and Next's own docs describe Proxy as something
 * that "can run outside of your application's main runtime" and that in
 * optimised cases is deployed to the CDN edge. Next 16 happens to default Proxy
 * to the Node.js runtime, but writing to the intersection of both runtimes is
 * what keeps one copy of the signing code valid on either side — a second,
 * edge-safe implementation that could drift from this one is exactly the kind of
 * duplication this app already has too much of.
 *
 * Note also that there is no `server-only` import here, unlike the rest of
 * `src/lib`. `server-only` resolves to a module that throws under the browser/
 * edge-light bundling conditions, and Proxy is bundled separately from the app;
 * marking this file would risk breaking the gate itself. Nothing in here is
 * secret on its own — both secrets are passed in by the caller, read from
 * non-`NEXT_PUBLIC_` env vars that are undefined in a browser bundle anyway.
 */

/** Name of the session cookie. Changing it logs everyone out. */
export const SESSION_COOKIE = "wp_dash_session";

/**
 * How long a login lasts. Twelve hours is long enough that the owner isn't
 * retyping a password all day, short enough that a cookie lifted off a shared
 * machine goes stale on its own. The expiry is inside the signed payload, not
 * just in the cookie's Max-Age — a client can edit or replay an expired
 * cookie's attributes, but it cannot forge the payload the HMAC covers.
 */
export const SESSION_TTL_SECONDS = 60 * 60 * 12;

const PAYLOAD_VERSION = 1;

export interface AuthEnv {
  password: string;
  sessionSecret: string;
}

/**
 * The two secrets the gate needs, or `null` if either is missing.
 *
 * Read per call rather than at module scope so that the Node.js-runtime Proxy
 * sees the live environment, and so a missing value can never be baked in as
 * "absent" at build time.
 *
 * Returning `null` rather than throwing is what makes the gate fail *closed*:
 * every caller treats `null` as "deny", so a deployment that forgets to set
 * these is locked rather than wide open. Neither value is ever logged.
 */
export function authEnv(): AuthEnv | null {
  const password = process.env.DASHBOARD_PASSWORD;
  const sessionSecret = process.env.SESSION_SECRET;
  if (!password || !sessionSecret) return null;
  return { password, sessionSecret };
}

// ------------------------------------------------------------ base64url

function b64urlEncode(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64urlDecode(value: string): Uint8Array {
  const base64 = value.replace(/-/g, "+").replace(/_/g, "/");
  const padding = base64.length % 4 === 0 ? "" : "=".repeat(4 - (base64.length % 4));
  const binary = atob(base64 + padding);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

// -------------------------------------------------------------- crypto

const encoder = new TextEncoder();

async function hmac(secret: string, message: string): Promise<Uint8Array> {
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return new Uint8Array(await crypto.subtle.sign("HMAC", key, encoder.encode(message)));
}

async function sha256(value: string): Promise<Uint8Array> {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", encoder.encode(value)));
}

/** Byte comparison whose running time does not depend on where the first
 *  difference is. Unequal lengths short-circuit: the length of an HMAC or a
 *  SHA-256 digest is a public constant, so it leaks nothing. */
function constantTimeEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

/**
 * Constant-time password check.
 *
 * Compares SHA-256 digests rather than the raw strings. A naive `===` on
 * strings leaks a prefix oracle *and* the password's length through timing;
 * hashing first makes both operands a fixed 32 bytes, so neither the length nor
 * the position of the first wrong character is observable.
 */
export async function passwordMatches(candidate: string, expected: string): Promise<boolean> {
  const [a, b] = await Promise.all([sha256(candidate), sha256(expected)]);
  return constantTimeEqual(a, b);
}

// ------------------------------------------------------------- sessions

/**
 * Mint a cookie value of the form `<base64url(payload)>.<base64url(hmac)>`.
 * The payload carries its own expiry so verification never has to trust the
 * client's cookie attributes.
 */
export async function signSession(
  sessionSecret: string,
  nowMs: number = Date.now(),
): Promise<string> {
  const issuedAt = Math.floor(nowMs / 1000);
  const payload = b64urlEncode(
    encoder.encode(
      JSON.stringify({ v: PAYLOAD_VERSION, iat: issuedAt, exp: issuedAt + SESSION_TTL_SECONDS }),
    ),
  );
  const signature = b64urlEncode(await hmac(sessionSecret, payload));
  return `${payload}.${signature}`;
}

/**
 * Verify a cookie value: signature first, then contents.
 *
 * The order matters. The payload is only parsed once its HMAC has checked out,
 * so unauthenticated input never reaches `JSON.parse` and a forged payload
 * cannot influence anything before it is rejected. Every failure path returns
 * `false`; nothing here throws, because a throw inside Proxy would fail the
 * request in a way that is harder to reason about than a plain "not logged in".
 */
export async function verifySession(
  value: string | undefined | null,
  sessionSecret: string,
): Promise<boolean> {
  if (!value) return false;

  const separator = value.lastIndexOf(".");
  if (separator <= 0 || separator === value.length - 1) return false;
  const payload = value.slice(0, separator);
  const signature = value.slice(separator + 1);

  try {
    const expected = await hmac(sessionSecret, payload);
    if (!constantTimeEqual(expected, b64urlDecode(signature))) return false;

    const claims: unknown = JSON.parse(new TextDecoder().decode(b64urlDecode(payload)));
    if (typeof claims !== "object" || claims === null) return false;
    const { v, exp } = claims as { v?: unknown; exp?: unknown };
    if (v !== PAYLOAD_VERSION || typeof exp !== "number") return false;
    return exp > Math.floor(Date.now() / 1000);
  } catch {
    // Malformed base64, malformed JSON, or an unavailable subtle crypto — all
    // of which mean "this is not a session we issued".
    return false;
  }
}

/**
 * Attributes for the session cookie.
 *
 * `secure` is conditional on the build being a production one, which is the
 * only concession here: Vercel serves this app over HTTPS exclusively, so the
 * deployed cookie is always Secure, while `next dev` over plain http://localhost
 * would silently refuse to store a Secure cookie and make local login
 * impossible to test.
 */
export function sessionCookieOptions() {
  return {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax" as const,
    path: "/",
    maxAge: SESSION_TTL_SECONDS,
  };
}
