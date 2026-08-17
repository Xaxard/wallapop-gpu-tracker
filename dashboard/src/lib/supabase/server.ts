import "server-only";
import { createClient } from "@supabase/supabase-js";

// No generated Database types exist for this project (the tracker owns the
// schema, in schema.sql, not a generated types file) — `any` keeps every
// table/column loosely typed instead of collapsing inserts/updates to `never`.
/* eslint-disable @typescript-eslint/no-explicit-any */
let cached: ReturnType<typeof createClient<any, any, any>> | null = null;

/**
 * Service-role Supabase client. Server-only (the `server-only` import makes
 * bundling this into a client component a build error). Never expose
 * SUPABASE_SERVICE_ROLE_KEY to the browser.
 */
export function supabaseAdmin() {
  if (cached) return cached;

  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!url || !key) {
    throw new Error("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not configured.");
  }

  cached = createClient<any, any, any>(url, key, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  return cached;
}
