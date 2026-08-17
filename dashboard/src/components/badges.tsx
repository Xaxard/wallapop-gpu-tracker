import { cn } from "@/lib/utils";
import type { Confidence } from "@/lib/constants";
import type { ListingStatus, AlertKind } from "@/lib/types";

export function StatusBadge({ status }: { status: ListingStatus | null }) {
  const map: Record<string, { label: string; className: string }> = {
    active: { label: "Active", className: "bg-foreground text-background border-foreground" },
    reserved: { label: "Reserved", className: "border-dashed border-foreground/40 text-foreground bg-transparent" },
    closed: { label: "Closed", className: "bg-muted text-muted-foreground border-border" },
  };
  const s = map[status ?? ""] ?? { label: status ?? "Unknown", className: "bg-muted text-muted-foreground border-border" };
  return (
    <span className={cn("inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium", s.className)}>
      {s.label}
    </span>
  );
}

export function ConfidenceBadge({ confidence }: { confidence: Confidence }) {
  return confidence === "ok" ? (
    <span className="inline-flex items-center rounded-full border border-foreground/20 bg-foreground/10 px-2 py-0.5 text-xs font-medium text-foreground">
      Learned
    </span>
  ) : (
    <span className="inline-flex items-center rounded-full border border-border bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
      Low confidence
    </span>
  );
}

export function AlertKindBadge({ kind }: { kind: AlertKind | null }) {
  if (kind === "price_drop") {
    return (
      <span className="inline-flex items-center rounded-full border border-foreground bg-foreground px-2 py-0.5 text-xs font-medium text-background">
        Price drop
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded-full border border-foreground/40 bg-transparent px-2 py-0.5 text-xs font-medium text-foreground">
      New
    </span>
  );
}
