"use client";

import { Bar, BarChart, CartesianGrid, XAxis, YAxis, Tooltip, ReferenceLine, ResponsiveContainer } from "recharts";
import type { HistogramBin } from "@/lib/chart-agg";
import { money } from "@/lib/format";

export function PriceDistributionChart({
  bins,
  refPrice,
  ceiling,
}: {
  bins: HistogramBin[];
  refPrice: number | null;
  ceiling: number | null;
}) {
  if (bins.length === 0) {
    return (
      <div className="flex h-56 items-center justify-center text-sm text-muted-foreground">
        No comps in the last 30 days.
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={224}>
      <BarChart data={bins} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
        <XAxis
          dataKey="label"
          tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
          axisLine={{ stroke: "var(--border)" }}
          tickLine={false}
          minTickGap={16}
        />
        <YAxis
          tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
          axisLine={false}
          tickLine={false}
          width={28}
          allowDecimals={false}
        />
        <Tooltip
          contentStyle={{
            background: "var(--popover)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            fontSize: 12,
          }}
          formatter={(value) => [String(value), "comps"]}
        />
        <Bar dataKey="count" fill="var(--muted-foreground)" radius={[3, 3, 0, 0]} />
        {refPrice !== null ? (
          <ReferenceLine
            x={bins.reduce((closest, b) => (Math.abs(b.rangeStart - refPrice) < Math.abs(closest.rangeStart - refPrice) ? b : closest), bins[0]).label}
            stroke="var(--foreground)"
            strokeWidth={1.5}
            label={{ value: `ref ${money(refPrice)}`, fontSize: 11, fill: "var(--foreground)", position: "top" }}
          />
        ) : null}
        {ceiling !== null ? (
          <ReferenceLine
            x={bins.reduce((closest, b) => (Math.abs(b.rangeStart - ceiling) < Math.abs(closest.rangeStart - ceiling) ? b : closest), bins[0]).label}
            stroke="var(--foreground)"
            strokeWidth={1.5}
            strokeDasharray="4 3"
            label={{ value: `ceiling ${money(ceiling)}`, fontSize: 11, fill: "var(--foreground)", position: "insideBottomLeft" }}
          />
        ) : null}
      </BarChart>
    </ResponsiveContainer>
  );
}
