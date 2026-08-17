import { cn } from "@/lib/utils";
import { moneySigned } from "@/lib/format";

export function NetChip({ label, value }: { label: string; value: number | null }) {
  if (value === null) {
    return (
      <span className="inline-flex items-center gap-1 rounded-md border border-border bg-muted px-1.5 py-0.5 text-xs tabular-nums text-muted-foreground">
        {label} —
      </span>
    );
  }
  const positive = value >= 0;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-xs font-medium tabular-nums",
        positive
          ? "border-foreground bg-foreground text-background"
          : "border-border text-muted-foreground",
      )}
    >
      {label} {moneySigned(value)}
    </span>
  );
}
