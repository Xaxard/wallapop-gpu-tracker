import { DealsTable } from "@/components/deals/deals-table";
import { getAlertsEnriched } from "@/lib/queries";

export const dynamic = "force-dynamic";

export default async function DealsPage() {
  const alerts = await getAlertsEnriched();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Deals & Alerts</h1>
        <p className="text-sm text-muted-foreground">
          Everything the bot has ever alerted on, newest first.
        </p>
      </div>
      <DealsTable alerts={alerts} />
    </div>
  );
}
