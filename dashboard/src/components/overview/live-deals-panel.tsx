import Link from "next/link";
import { ExternalLink } from "lucide-react";
import type { LiveDeal } from "@/lib/queries";
import { NetChip } from "@/components/net-chip";
import { Thumb } from "@/components/thumb";
import { money } from "@/lib/format";

export function LiveDealsPanel({
  deals,
  activeSearches,
  truncated = false,
  scanned = 0,
  candidates = 0,
}: {
  deals: LiveDeal[];
  activeSearches: number;
  /** The scan hit its safety bound, so this list may be missing deals. Said out
   *  loud rather than swallowed — the previous version of this query silently
   *  dropped everything past its 500-row limit. */
  truncated?: boolean;
  scanned?: number;
  candidates?: number;
}) {
  const truncationNotice = truncated ? (
    <p className="border-t py-2 text-xs text-destructive">
      Showing deals from the {scanned.toLocaleString()} cheapest of{" "}
      {candidates.toLocaleString()} active listings under the price cap — the scan limit was
      reached, so this list may be incomplete. Raise MAX_SCAN_ROWS in
      src/lib/queries.ts.
    </p>
  ) : null;

  if (deals.length === 0) {
    return (
      <>
        <p className="py-8 text-center text-sm text-muted-foreground">
          No live deals right now — watching {activeSearches} search
          {activeSearches === 1 ? "" : "es"}.
        </p>
        {truncationNotice}
      </>
    );
  }

  return (
    <>
      <ul className="divide-y">
      {deals.map((d) => (
        <li key={d.item_id} className="flex items-center gap-3 py-3">
          <Thumb src={d.image_url} alt={d.title ?? ""} className="size-12" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium">{d.title}</p>
            <div className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
              <span>{d.model_key ?? "unclassified"}</span>
              <span>·</span>
              <span className="tabular-nums font-medium text-foreground">{money(d.last_price)}</span>
              {/* What actually qualified this listing: the tracker gates on a
                  haggled offer, not the asking price. */}
              {d.offer_price !== null ? (
                <>
                  <span>·</span>
                  <span className="tabular-nums">offer {money(d.offer_price)}</span>
                </>
              ) : null}
              {d.is_seed ? <span className="text-xs">· seed price</span> : null}
            </div>
          </div>
          <div className="hidden shrink-0 items-center gap-1.5 sm:flex">
            <NetChip label="ship" value={d.net_shipped} />
            <NetChip label="in-p" value={d.net_inperson} />
          </div>
          {d.web_url ? (
            <Link
              href={d.web_url}
              target="_blank"
              rel="noreferrer"
              className="shrink-0 text-muted-foreground hover:text-foreground"
              aria-label="View on Wallapop"
            >
              <ExternalLink className="size-4" />
            </Link>
          ) : null}
        </li>
      ))}
      </ul>
      {truncationNotice}
    </>
  );
}
