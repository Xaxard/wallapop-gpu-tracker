import type { ObservationRow } from "@/lib/types";

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
}

export interface PriceHistoryPoint {
  date: string; // yyyy-mm-dd
  activeMedian: number | null;
  reservedMedian: number | null;
}

export function buildPriceHistory(observations: ObservationRow[]): PriceHistoryPoint[] {
  const byDay = new Map<string, { active: number[]; reserved: number[] }>();
  for (const o of observations) {
    if (o.price === null) continue;
    const day = o.seen_at.slice(0, 10);
    const bucket = byDay.get(day) ?? { active: [], reserved: [] };
    if (o.status === "reserved") bucket.reserved.push(o.price);
    else bucket.active.push(o.price);
    byDay.set(day, bucket);
  }
  return [...byDay.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, bucket]) => ({
      date,
      activeMedian: median(bucket.active),
      reservedMedian: median(bucket.reserved),
    }));
}

export interface HistogramBin {
  label: string;
  rangeStart: number;
  count: number;
}

export function buildHistogram(observations: ObservationRow[], binSize = 25): HistogramBin[] {
  const prices = observations.map((o) => o.price).filter((p): p is number => p !== null);
  if (prices.length === 0) return [];
  const min = Math.floor(Math.min(...prices) / binSize) * binSize;
  const max = Math.ceil(Math.max(...prices) / binSize) * binSize;
  const bins = new Map<number, number>();
  for (let b = min; b < max; b += binSize) bins.set(b, 0);
  for (const p of prices) {
    const bucket = Math.floor(p / binSize) * binSize;
    bins.set(bucket, (bins.get(bucket) ?? 0) + 1);
  }
  return [...bins.entries()]
    .sort(([a], [b]) => a - b)
    .map(([rangeStart, count]) => ({
      label: `€${rangeStart}`,
      rangeStart,
      count,
    }));
}
