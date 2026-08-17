"use client";

import { useEffect, useState, useTransition } from "react";
import { cn } from "@/lib/utils";
import { ConfidenceBadge } from "@/components/badges";
import { money, relativeTime } from "@/lib/format";
import { ceilingInPerson, confidenceForRow, ownCompCount } from "@/lib/constants";
import type { ModelPriceRow } from "@/lib/types";
import { getModelDetail } from "@/lib/actions/model-detail";
import { PriceHistoryChart } from "@/components/models/price-history-chart";
import { PriceDistributionChart } from "@/components/models/price-distribution-chart";
import type { PriceHistoryPoint, HistogramBin } from "@/lib/chart-agg";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface Detail {
  model: ModelPriceRow | null;
  confidence: "ok" | "low";
  ownComps: number;
  totalComps: number;
  history: PriceHistoryPoint[];
  histogram: HistogramBin[];
  observationCount: number;
}

export function ModelsExplorer({ models }: { models: ModelPriceRow[] }) {
  const [selected, setSelected] = useState<string | null>(models[0]?.model_key ?? null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [pending, startTransition] = useTransition();

  function select(modelKey: string) {
    setSelected(modelKey);
    startTransition(async () => {
      const d = await getModelDetail(modelKey);
      setDetail(d);
    });
  }

  useEffect(() => {
    if (!selected) return;
    startTransition(async () => {
      const d = await getModelDetail(selected);
      setDetail(d);
    });
    // Fetch detail for the initial default selection only; row clicks go through select().
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_420px]">
      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Model</TableHead>
              <TableHead className="text-right">Ref price</TableHead>
              <TableHead className="text-right">Ceiling (ship)</TableHead>
              <TableHead className="text-right">Ceiling (in-p)</TableHead>
              <TableHead className="text-right">Comps</TableHead>
              <TableHead>Confidence</TableHead>
              <TableHead className="text-right">Updated</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {models.map((m) => (
              <TableRow
                key={m.model_key}
                onClick={() => select(m.model_key)}
                className={cn(
                  "cursor-pointer hover:bg-muted/40",
                  selected === m.model_key && "bg-muted/60",
                )}
              >
                <TableCell className="font-medium">{m.model_key}</TableCell>
                <TableCell className="text-right tabular-nums">{money(m.ref_price)}</TableCell>
                <TableCell className="text-right tabular-nums">{money(m.buy_ceiling)}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {money(
                    m.buy_ceiling_in_person ??
                      (m.ref_price !== null
                        ? ceilingInPerson(m.ref_price, m.is_seed === true)
                        : null),
                  )}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {ownCompCount(m)}
                  {/* Borrowed comps counted separately: a ceiling resting on one
                      of its own comps plus eleven from a sibling SKU is a much
                      weaker claim than twelve of its own. */}
                  {m.n_comps !== null && m.n_comps > ownCompCount(m) ? (
                    <span className="ml-1 text-xs text-muted-foreground">
                      +{m.n_comps - ownCompCount(m)}
                    </span>
                  ) : null}
                </TableCell>
                <TableCell>
                  <ConfidenceBadge confidence={confidenceForRow(m)} />
                </TableCell>
                <TableCell className="text-right text-xs text-muted-foreground">
                  {relativeTime(m.updated_at)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <div className="rounded-lg border bg-card p-4">
        {!selected ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Select a model to inspect it.</p>
        ) : (
          <div className={cn("space-y-5", pending && "opacity-60")}>
            <div>
              <h3 className="text-sm font-semibold">{selected}</h3>
              {detail?.model ? (
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {detail.observationCount} observations · {detail.ownComps} own comps
                  {detail.totalComps > detail.ownComps
                    ? ` · ${detail.totalComps - detail.ownComps} borrowed`
                    : ""}
                </p>
              ) : null}
            </div>

            <div>
              <p className="mb-2 text-xs font-medium text-muted-foreground">
                Price history (median/day, 90d)
              </p>
              {detail ? (
                <PriceHistoryChart data={detail.history} />
              ) : (
                <div className="h-56 animate-pulse rounded-md bg-muted" />
              )}
            </div>

            <div>
              <p className="mb-2 text-xs font-medium text-muted-foreground">
                Comp distribution (30d)
              </p>
              {detail ? (
                <PriceDistributionChart
                  bins={detail.histogram}
                  refPrice={detail.model?.ref_price ?? null}
                  ceiling={detail.model?.buy_ceiling ?? null}
                />
              ) : (
                <div className="h-56 animate-pulse rounded-md bg-muted" />
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
