"use server";

import { getModel, getModelObservations } from "@/lib/queries";
import { buildHistogram, buildPriceHistory } from "@/lib/chart-agg";
import { confidenceForRow, ownCompCount } from "@/lib/constants";
import { requireSession } from "@/lib/auth/require-session";

// Read-only, but still a POST endpoint whose action id ships in the client
// bundle — so it is gated on the session directly, not just by src/proxy.ts.
export async function getModelDetail(modelKey: string) {
  await requireSession();
  const [model, observations] = await Promise.all([
    getModel(modelKey),
    getModelObservations(modelKey, 90),
  ]);

  return {
    model,
    confidence: model ? confidenceForRow(model) : ("low" as const),
    // Own comps where the tracker records them, total otherwise — see
    // ownCompCount(). Borrowed sibling comps say much less about this SKU.
    ownComps: model ? ownCompCount(model) : 0,
    totalComps: model?.n_comps ?? 0,
    history: buildPriceHistory(observations),
    histogram: buildHistogram(observations.filter((o) => o.seen_at >= daysAgoIso(30))),
    observationCount: observations.length,
  };
}

function daysAgoIso(days: number): string {
  return new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString();
}
