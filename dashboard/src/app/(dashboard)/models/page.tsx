import { ModelsExplorer } from "@/components/models/models-explorer";
import { getModels } from "@/lib/queries";

export const dynamic = "force-dynamic";

export default async function ModelsPage() {
  const models = await getModels();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Models</h1>
        <p className="text-sm text-muted-foreground">
          Pricing engine health — one row per GPU model the tracker prices.
        </p>
      </div>
      {models.length === 0 ? (
        <p className="py-10 text-center text-sm text-muted-foreground">
          No models priced yet — the comps loop hasn&apos;t run.
        </p>
      ) : (
        <ModelsExplorer models={models} />
      )}
    </div>
  );
}
