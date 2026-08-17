import { cn } from "@/lib/utils";
import type { LucideIcon } from "lucide-react";

export function KpiCard({
  label,
  value,
  sub,
  icon: Icon,
  tone = "default",
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  icon?: LucideIcon;
  tone?: "default" | "highlight";
}) {
  return (
    <div className={cn("rounded-lg border bg-card p-4", tone === "highlight" && "border-foreground/30")}>
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
        {Icon ? (
          <Icon
            className={cn("size-3.5", tone === "highlight" ? "text-foreground" : "text-muted-foreground")}
          />
        ) : null}
      </div>
      <div className="mt-2 flex items-center gap-1.5 text-2xl font-semibold tabular-nums tracking-tight text-foreground">
        {tone === "highlight" ? (
          <span className="size-1.5 rounded-full bg-foreground" aria-hidden />
        ) : null}
        {value}
      </div>
      {sub ? <div className="mt-1 text-xs text-muted-foreground">{sub}</div> : null}
    </div>
  );
}
