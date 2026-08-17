"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ExternalLink } from "lucide-react";
import type { EnrichedAlert } from "@/lib/queries";
import { AlertKindBadge } from "@/components/badges";
import { NetChip } from "@/components/net-chip";
import { Thumb } from "@/components/thumb";
import { money, compactDateTime } from "@/lib/format";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function DealsTable({ alerts }: { alerts: EnrichedAlert[] }) {
  const models = useMemo(
    () => [...new Set(alerts.map((a) => a.listing?.model_key).filter((m): m is string => !!m))].sort(),
    [alerts],
  );

  const [model, setModel] = useState<string>("all");
  const [kind, setKind] = useState<string>("all");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");

  const filtered = useMemo(() => {
    return alerts.filter((a) => {
      if (model !== "all" && a.listing?.model_key !== model) return false;
      if (kind !== "all" && a.kind !== kind) return false;
      if (from && a.sent_at < from) return false;
      if (to && a.sent_at > `${to}T23:59:59`) return false;
      return true;
    });
  }, [alerts, model, kind, from, to]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">Model</Label>
          <Select value={model} onValueChange={(v) => setModel(v ?? "all")}>
            <SelectTrigger size="sm" className="w-40">
              <SelectValue placeholder="All models" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All models</SelectItem>
              {models.map((m) => (
                <SelectItem key={m} value={m}>
                  {m}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">Kind</Label>
          <Select value={kind} onValueChange={(v) => setKind(v ?? "all")}>
            <SelectTrigger size="sm" className="w-36">
              <SelectValue placeholder="All kinds" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All kinds</SelectItem>
              <SelectItem value="new">New</SelectItem>
              <SelectItem value="price_drop">Price drop</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">From</Label>
          <Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="h-8 w-36" />
        </div>
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">To</Label>
          <Input type="date" value={to} onChange={(e) => setTo(e.target.value)} className="h-8 w-36" />
        </div>
        <span className="pb-1.5 text-xs text-muted-foreground">
          {filtered.length} of {alerts.length}
        </span>
      </div>

      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader className="sticky top-0 bg-card">
            <TableRow>
              <TableHead className="hidden sm:table-cell">Time</TableHead>
              <TableHead></TableHead>
              <TableHead>Title</TableHead>
              <TableHead className="hidden md:table-cell">Model</TableHead>
              <TableHead className="text-right">Price</TableHead>
              <TableHead className="text-right">Net (ship)</TableHead>
              <TableHead className="hidden text-right lg:table-cell">Net (in-p)</TableHead>
              <TableHead>Kind</TableHead>
              <TableHead></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.length === 0 ? (
              <TableRow>
                <TableCell colSpan={9} className="py-10 text-center text-sm text-muted-foreground">
                  {alerts.length === 0
                    ? "No alerts sent yet — nothing to show until the bot fires its first one."
                    : "No alerts match these filters."}
                </TableCell>
              </TableRow>
            ) : (
              filtered.map((a) => (
                <TableRow key={a.id} className="hover:bg-muted/40">
                  <TableCell className="hidden whitespace-nowrap text-xs text-muted-foreground tabular-nums sm:table-cell">
                    {compactDateTime(a.sent_at)}
                  </TableCell>
                  <TableCell>
                    <Thumb src={a.listing?.image_url} alt="" className="size-8" />
                  </TableCell>
                  <TableCell className="max-w-64 truncate text-sm">{a.listing?.title ?? a.item_id}</TableCell>
                  <TableCell className="hidden text-sm text-muted-foreground md:table-cell">{a.listing?.model_key ?? "—"}</TableCell>
                  <TableCell className="text-right text-sm tabular-nums">
                    {a.kind === "price_drop" && a.prev_price ? (
                      <>
                        <span className="mr-1.5 text-muted-foreground line-through">{money(a.prev_price)}</span>
                        {money(a.price)}
                      </>
                    ) : (
                      money(a.price)
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <NetChip label="" value={a.net_shipped} />
                  </TableCell>
                  <TableCell className="hidden text-right lg:table-cell">
                    <NetChip label="" value={a.net_inperson} />
                  </TableCell>
                  <TableCell>
                    <AlertKindBadge kind={a.kind} />
                  </TableCell>
                  <TableCell>
                    {a.listing?.web_url ? (
                      <Link href={a.listing.web_url} target="_blank" rel="noreferrer" className="text-muted-foreground hover:text-foreground">
                        <ExternalLink className="size-4" />
                      </Link>
                    ) : null}
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
