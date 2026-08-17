import type { SearchWithCounts } from "@/lib/queries";
import { SearchToggle } from "@/components/searches/search-toggle";
import { cn } from "@/lib/utils";

const ROLE_LABEL: Record<string, string> = {
  alert: "Alert searches",
  comps: "Comps searches",
};

export function SearchesList({ searches }: { searches: SearchWithCounts[] }) {
  const groups = new Map<string, SearchWithCounts[]>();
  for (const s of searches) {
    const arr = groups.get(s.role) ?? [];
    arr.push(s);
    groups.set(s.role, arr);
  }

  return (
    <div className="space-y-6">
      {[...groups.entries()].map(([role, rows]) => (
        <section key={role} className="rounded-lg border bg-card">
          <div className="border-b px-4 py-3">
            <h2 className="text-sm font-semibold">{ROLE_LABEL[role] ?? role}</h2>
          </div>
          <ul className="divide-y">
            {rows.map((s) => (
              <li key={s.id} className="flex flex-wrap items-center gap-4 px-4 py-3">
                <div className="min-w-48 flex-1">
                  <p className={cn("text-sm font-medium", !s.active && "text-muted-foreground")}>
                    {s.label}
                  </p>
                  <p className="mt-0.5 truncate text-xs text-muted-foreground">{s.keywords}</p>
                </div>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                  <span>{s.model_key ?? "any model"}</span>
                  <span className="tabular-nums">
                    €{s.min_price ?? 0}–{s.max_price ?? "∞"}
                  </span>
                  <span className="tabular-nums">{s.distance_km ?? "—"} km</span>
                  <span className="tabular-nums">
                    {s.listingsCount === null ? "— listings" : `${s.listingsCount} listings*`}
                  </span>
                  <span className="tabular-nums">
                    {s.alertsCount === null ? "— alerts" : `${s.alertsCount} alerts*`}
                  </span>
                </div>
                <SearchToggle id={s.id} active={s.active} />
              </li>
            ))}
          </ul>
        </section>
      ))}
      <p className="text-xs text-muted-foreground">
        * Listing/alert counts are approximate — matched by the search&apos;s target model, since
        listings aren&apos;t tagged with the search that discovered them. Searches without a fixed
        model show as &ldquo;—&rdquo;.
      </p>
    </div>
  );
}
