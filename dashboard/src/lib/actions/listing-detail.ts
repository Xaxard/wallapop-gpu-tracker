"use server";

import { getListing, getListingObservations } from "@/lib/queries";
import { requireSession } from "@/lib/auth/require-session";

// Read-only, but still a POST endpoint whose action id ships in the client
// bundle — so it is gated on the session directly, not just by src/proxy.ts.
export async function getListingDetail(itemId: string) {
  await requireSession();
  const [listing, observations] = await Promise.all([
    getListing(itemId),
    getListingObservations(itemId),
  ]);
  return { listing, observations };
}
