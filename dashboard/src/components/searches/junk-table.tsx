"use client";

import { useMemo, useState } from "react";
import { Flag } from "lucide-react";
import { cn } from "@/lib/utils";
import type { JunkExclusionRow } from "@/lib/types";
import { compactDateTime } from "@/lib/format";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";

const RULE_STYLE: Record<string, string> = {
  BUNDLE: "border-foreground bg-foreground text-background",
  LAPTOP: "border-foreground bg-foreground text-background",
  DEFECT: "border-border bg-muted text-muted-foreground",
  TRADE: "border-border bg-muted text-muted-foreground",
  NOT_A_CARD: "border-border bg-muted text-muted-foreground",
  WANTED: "border-border bg-muted text-muted-foreground",
};

function RuleBadge({ rule }: { rule: string | null }) {
  const style = RULE_STYLE[rule ?? ""] ?? "border-border bg-muted text-muted-foreground";
  return (
    <span className={cn("inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium", style)}>
      {rule ?? "?"}
    </span>
  );
}

export function JunkTable({ rows }: { rows: JunkExclusionRow[] }) {
  const rules = useMemo(
    () => [...new Set(rows.map((r) => r.rule).filter((r): r is string => !!r))].sort(),
    [rows],
  );
  const [rule, setRule] = useState("all");
  const [flagged, setFlagged] = useState<Set<number>>(new Set());

  const filtered = rule === "all" ? rows : rows.filter((r) => r.rule === rule);

  function toggleFlag(id: number) {
    setFlagged((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div className="space-y-4">
      <div className="flex items-end gap-3">
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">Rule</Label>
          <Select value={rule} onValueChange={(v) => setRule(v ?? "all")}>
            <SelectTrigger size="sm" className="w-40">
              <SelectValue placeholder="All rules" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All rules</SelectItem>
              {rules.map((r) => (
                <SelectItem key={r} value={r}>
                  {r}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <span className="pb-1.5 text-xs text-muted-foreground">{filtered.length} excluded</span>
      </div>

      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Time</TableHead>
              <TableHead>Title</TableHead>
              <TableHead>Rule</TableHead>
              <TableHead>Matched</TableHead>
              <TableHead className="text-right">Wrong?</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="py-10 text-center text-sm text-muted-foreground">
                  Nothing excluded for this rule.
                </TableCell>
              </TableRow>
            ) : (
              filtered.map((r) => (
                <TableRow key={r.id} className="hover:bg-muted/40">
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground tabular-nums">
                    {compactDateTime(r.seen_at)}
                  </TableCell>
                  <TableCell className="max-w-96 truncate text-sm">{r.title}</TableCell>
                  <TableCell>
                    <RuleBadge rule={r.rule} />
                  </TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">{r.matched}</TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-7"
                      aria-label="Flag as wrongly excluded"
                      onClick={() => toggleFlag(r.id)}
                    >
                      <Flag
                        className={cn(
                          "size-3.5",
                          flagged.has(r.id) ? "fill-foreground text-foreground" : "text-muted-foreground",
                        )}
                      />
                    </Button>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
