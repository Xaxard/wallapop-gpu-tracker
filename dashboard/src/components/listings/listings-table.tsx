"use client";

import { useMemo, useState, useTransition } from "react";
import Link from "next/link";
import { ExternalLink } from "lucide-react";
import type { ListingRow, ObservationRow } from "@/lib/types";
import { StatusBadge } from "@/components/badges";
import { Thumb } from "@/components/thumb";
import { money, dateOnly, relativeTime } from "@/lib/format";
import { getListingDetail } from "@/lib/actions/listing-detail";
import { ObservationTimelineChart } from "@/components/listings/observation-timeline-chart";
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
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "@/components/ui/sheet";

export function ListingsTable({ listings }: { listings: ListingRow[] }) {
  const models = useMemo(
    () => [...new Set(listings.map((l) => l.model_key).filter((m): m is string => !!m))].sort(),
    [listings],
  );

  const [model, setModel] = useState("all");
  const [status, setStatus] = useState("all");
  const [minPrice, setMinPrice] = useState("");
  const [maxPrice, setMaxPrice] = useState("");
  const [query, setQuery] = useState("");

  const [open, setOpen] = useState(false);
  const [activeListing, setActiveListing] = useState<ListingRow | null>(null);
  const [observations, setObservations] = useState<ObservationRow[] | null>(null);
  const [pending, startTransition] = useTransition();

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const min = minPrice ? Number(minPrice) : null;
    const max = maxPrice ? Number(maxPrice) : null;
    return listings.filter((l) => {
      if (model !== "all" && l.model_key !== model) return false;
      if (status !== "all" && l.last_status !== status) return false;
      if (min !== null && (l.last_price ?? -Infinity) < min) return false;
      if (max !== null && (l.last_price ?? Infinity) > max) return false;
      if (q && !(l.title ?? "").toLowerCase().includes(q)) return false;
      return true;
    });
  }, [listings, model, status, minPrice, maxPrice, query]);

  function openListing(listing: ListingRow) {
    setActiveListing(listing);
    setObservations(null);
    setOpen(true);
    startTransition(async () => {
      const { observations } = await getListingDetail(listing.item_id);
      setObservations(observations);
    });
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">Search</Label>
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Title contains…"
            className="h-8 w-48"
          />
        </div>
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
          <Label className="text-xs text-muted-foreground">Status</Label>
          <Select value={status} onValueChange={(v) => setStatus(v ?? "all")}>
            <SelectTrigger size="sm" className="w-32">
              <SelectValue placeholder="All statuses" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              <SelectItem value="active">Active</SelectItem>
              <SelectItem value="reserved">Reserved</SelectItem>
              <SelectItem value="closed">Closed</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">Min €</Label>
          <Input
            type="number"
            value={minPrice}
            onChange={(e) => setMinPrice(e.target.value)}
            className="h-8 w-24"
          />
        </div>
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">Max €</Label>
          <Input
            type="number"
            value={maxPrice}
            onChange={(e) => setMaxPrice(e.target.value)}
            className="h-8 w-24"
          />
        </div>
        <span className="pb-1.5 text-xs text-muted-foreground">
          {filtered.length} of {listings.length}
        </span>
      </div>

      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead></TableHead>
              <TableHead>Title</TableHead>
              <TableHead className="hidden md:table-cell">Model</TableHead>
              <TableHead className="text-right">Price</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="hidden text-right lg:table-cell">First seen</TableHead>
              <TableHead className="hidden text-right sm:table-cell">Last seen</TableHead>
              <TableHead></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.length === 0 ? (
              <TableRow>
                <TableCell colSpan={8} className="py-10 text-center text-sm text-muted-foreground">
                  {listings.length === 0
                    ? "No listings tracked yet — nothing to show until a search loop runs."
                    : "No listings match these filters."}
                </TableCell>
              </TableRow>
            ) : (
              filtered.map((l) => (
                <TableRow
                  key={l.item_id}
                  className="cursor-pointer hover:bg-muted/40"
                  onClick={() => openListing(l)}
                >
                  <TableCell>
                    <Thumb src={l.image_url} alt="" className="size-8" />
                  </TableCell>
                  <TableCell className="max-w-72 truncate text-sm">{l.title}</TableCell>
                  <TableCell className="hidden text-sm text-muted-foreground md:table-cell">{l.model_key ?? "—"}</TableCell>
                  <TableCell className="text-right text-sm tabular-nums">{money(l.last_price)}</TableCell>
                  <TableCell>
                    <StatusBadge status={l.last_status} />
                  </TableCell>
                  <TableCell className="hidden text-right text-xs text-muted-foreground lg:table-cell">
                    {dateOnly(l.first_seen)}
                  </TableCell>
                  <TableCell className="hidden text-right text-xs text-muted-foreground sm:table-cell">
                    {relativeTime(l.last_seen)}
                  </TableCell>
                  <TableCell>
                    {l.web_url ? (
                      <Link
                        href={l.web_url}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="text-muted-foreground hover:text-foreground"
                      >
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

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="right" className="w-full sm:max-w-lg">
          <SheetHeader>
            <SheetTitle className="pr-8">{activeListing?.title}</SheetTitle>
            <SheetDescription>
              {activeListing?.model_key ?? "unclassified"} · {money(activeListing?.last_price)}
              {activeListing ? (
                <>
                  {" "}
                  · <StatusBadgeInline status={activeListing.last_status} />
                </>
              ) : null}
            </SheetDescription>
          </SheetHeader>
          <div className="space-y-4 px-4 pb-4">
            <div className="flex items-center gap-3">
              <Thumb src={activeListing?.image_url} alt="" className="size-16" />
              <div className="min-w-0 flex-1 text-xs text-muted-foreground">
                <p>First seen {dateOnly(activeListing?.first_seen)}</p>
                <p>Last seen {relativeTime(activeListing?.last_seen)}</p>
                {activeListing?.web_url ? (
                  <Link
                    href={activeListing.web_url}
                    target="_blank"
                    rel="noreferrer"
                    className="mt-1 inline-flex items-center gap-1 text-foreground hover:underline"
                  >
                    View on Wallapop <ExternalLink className="size-3" />
                  </Link>
                ) : null}
              </div>
            </div>

            <div>
              <p className="mb-2 text-xs font-medium text-muted-foreground">Observation timeline</p>
              {observations === null ? (
                <div className={pending ? "h-48 animate-pulse rounded-md bg-muted" : "h-48"} />
              ) : (
                <ObservationTimelineChart observations={observations} />
              )}
            </div>
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}

function StatusBadgeInline({ status }: { status: ListingRow["last_status"] }) {
  return <StatusBadge status={status} />;
}
