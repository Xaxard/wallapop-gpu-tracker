"use server";

import { revalidatePath } from "next/cache";
import { supabaseAdmin } from "@/lib/supabase/server";
import { requireSession } from "@/lib/auth/require-session";

/**
 * The only write in the entire dashboard: enabling/disabling one of the
 * tracker's searches.
 *
 * The session check is repeated here rather than left to `src/proxy.ts`. A
 * Server Action is a POST to whatever route imports it, with an action id that
 * is present in the client bundle and callable by anyone who reads it — so
 * before any gate existed, a visitor could turn the owner's alert searches off
 * and the feed would simply go quiet. Proxy now blocks that, but a matcher edit
 * or a refactor that moves this action to another route would silently remove
 * the protection, and this call mutates live bot configuration. It gets its own
 * check.
 */
export async function toggleSearchActive(id: number, active: boolean) {
  await requireSession();
  const { error } = await supabaseAdmin().from("searches").update({ active }).eq("id", id);
  if (error) throw error;
  revalidatePath("/searches");
}
