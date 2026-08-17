"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ExternalLink } from "lucide-react";
import type { EnrichedAlert } from "@/lib/queries";
import { AlertKindBadge } from "@/components/badges";
import { NetChip } from "@/components/net-chip";
import { Thumb } from "@/components/thumb";
import { money, relativeTime } from "@/lib/format";
import { cn } from "@/lib/utils";

const POLL_MS = 8000;

export function AlertsFeed({ initial }: { initial: EnrichedAlert[] }) {
  const [alerts, setAlerts] = useState(initial);
  const knownIds = useRef(new Set(initial.map((a) => a.id)));
  const [freshIds, setFreshIds] = useState<Set<number>>(new Set());

  useEffect(() => {
    const timer = setInterval(async () => {
      try {
        const res = await fetch("/api/alerts/recent", { cache: "no-store" });
        // A 12-hour session will expire while this page is open. Without this
        // the poller would just quietly stop returning data and the feed would
        // look like a dead market; reloading lets proxy.ts bounce to /login.
        if (res.status === 401) {
          window.location.reload();
          return;
        }
        if (!res.ok) return;
        const { alerts: next } = (await res.json()) as { alerts: EnrichedAlert[] };
        const fresh = next.filter((a) => !knownIds.current.has(a.id));
        if (fresh.length > 0) {
          for (const a of fresh) knownIds.current.add(a.id);
          setFreshIds(new Set(fresh.map((a) => a.id)));
          setTimeout(() => setFreshIds(new Set()), 1200);
        }
        setAlerts(next);
      } catch {
        // transient network error — next poll will retry
      }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, []);

  if (alerts.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        No alerts sent yet — the bot will post here the moment one fires.
      </p>
    );
  }

  return (
    <ul className="divide-y">
      {alerts.map((a) => (
        <li
          key={a.id}
          className={cn(
            "flex items-center gap-3 py-3",
            freshIds.has(a.id) && "animate-in fade-in slide-in-from-top-2 duration-500",
          )}
        >
          <Thumb src={a.listing?.image_url} alt={a.listing?.title ?? ""} className="size-10" />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <AlertKindBadge kind={a.kind} />
              <span className="truncate text-sm font-medium">{a.listing?.title ?? a.item_id}</span>
            </div>
            <div className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
              <span className="tabular-nums">{a.listing?.model_key ?? "unclassified"}</span>
              <span>·</span>
              <span className="tabular-nums">
                {a.kind === "price_drop" && a.prev_price ? (
                  <>
                    <span className="line-through">{money(a.prev_price)}</span> {money(a.price)}
                  </>
                ) : (
                  money(a.price)
                )}
              </span>
              <span>·</span>
              <span>{relativeTime(a.sent_at)}</span>
            </div>
          </div>
          <div className="hidden shrink-0 items-center gap-1.5 sm:flex">
            <NetChip label="ship" value={a.net_shipped} />
            <NetChip label="in-p" value={a.net_inperson} />
          </div>
          {a.listing?.web_url ? (
            <Link
              href={a.listing.web_url}
              target="_blank"
              rel="noreferrer"
              className="shrink-0 text-muted-foreground hover:text-foreground"
            >
              <ExternalLink className="size-4" />
            </Link>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
