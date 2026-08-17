import { NextResponse } from "next/server";
import { getAlertsEnriched } from "@/lib/queries";
import { hasValidSession } from "@/lib/auth/require-session";

// Polled by the client-side alerts feed on the Overview page to fake a
// "live" feed without shipping any Supabase credentials to the browser
// (see dashboard/README.md — there is no public anon key/RLS set up for
// this project, so a real client-side Realtime subscription isn't safe here).
//
// src/proxy.ts deliberately does not exclude /api from its matcher, so this is
// already gated; the check is repeated here because this route returns the same
// alert history the pages do and should not depend on a matcher staying correct.
export async function GET() {
  if (!(await hasValidSession())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const alerts = await getAlertsEnriched(15);
  return NextResponse.json({ alerts });
}
