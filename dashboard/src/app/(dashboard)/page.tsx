import { Zap, Bell, ListChecks, Cpu, FilterX } from "lucide-react";
import { KpiCard } from "@/components/kpi-card";
import { LiveDealsPanel } from "@/components/overview/live-deals-panel";
import { AlertsFeed } from "@/components/overview/alerts-feed";
import { getAlertsEnriched, getLiveDeals, getOverviewKpis } from "@/lib/queries";

export const dynamic = "force-dynamic";

export default async function OverviewPage() {
  const liveDeals = await getLiveDeals();
  const [kpis, recentAlerts] = await Promise.all([
    getOverviewKpis(liveDeals.deals.length),
    getAlertsEnriched(15),
  ]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Overview</h1>
        <p className="text-sm text-muted-foreground">
          Live state of the deal-tracker and its pricing engine.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <KpiCard
          label="Live deals now"
          value={kpis.liveDealsCount}
          icon={Zap}
          tone={kpis.liveDealsCount > 0 ? "highlight" : "default"}
        />
        <KpiCard
          label="Alerts"
          value={kpis.alerts24h}
          sub={`${kpis.alerts7d} in 7d`}
          icon={Bell}
        />
        <KpiCard
          label="Listings tracked"
          value={kpis.listingsActive}
          sub={`${kpis.listingsTotal} total`}
          icon={ListChecks}
        />
        <KpiCard
          label="Models priced"
          value={kpis.modelsOk}
          sub={`${kpis.modelsLow} low-confidence`}
          icon={Cpu}
        />
        <KpiCard label="Junk filtered" value={kpis.junk7d} sub="last 7d" icon={FilterX} />
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <section className="rounded-lg border bg-card">
          <div className="flex items-center justify-between border-b px-4 py-3">
            <h2 className="text-sm font-semibold">Live deals</h2>
            <span className="text-xs text-muted-foreground">{liveDeals.deals.length} now</span>
          </div>
          <div className="px-4">
            <LiveDealsPanel
              deals={liveDeals.deals}
              activeSearches={kpis.activeSearches}
              truncated={liveDeals.truncated}
              scanned={liveDeals.scanned}
              candidates={liveDeals.candidates}
            />
          </div>
        </section>

        <section className="rounded-lg border bg-card">
          <div className="flex items-center justify-between border-b px-4 py-3">
            <h2 className="text-sm font-semibold">Recent alerts</h2>
            <span className="text-xs text-muted-foreground">last 15</span>
          </div>
          <div className="px-4">
            <AlertsFeed initial={recentAlerts} />
          </div>
        </section>
      </div>
    </div>
  );
}
