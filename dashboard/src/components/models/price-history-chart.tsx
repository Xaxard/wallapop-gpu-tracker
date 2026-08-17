"use client";

import {
  Line,
  LineChart,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import type { PriceHistoryPoint } from "@/lib/chart-agg";
import { dateOnly, money } from "@/lib/format";

export function PriceHistoryChart({ data }: { data: PriceHistoryPoint[] }) {
  if (data.length === 0) {
    return (
      <div className="flex h-56 items-center justify-center text-sm text-muted-foreground">
        No observations yet in this window.
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={224}>
      <LineChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
        <XAxis
          dataKey="date"
          tickFormatter={(d) => dateOnly(d)}
          tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
          axisLine={{ stroke: "var(--border)" }}
          tickLine={false}
          minTickGap={32}
        />
        <YAxis
          tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
          axisLine={false}
          tickLine={false}
          width={44}
          tickFormatter={(v) => `€${v}`}
        />
        <Tooltip
          contentStyle={{
            background: "var(--popover)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            fontSize: 12,
          }}
          labelFormatter={(d) => dateOnly(d as string)}
          formatter={(value, name) => [money(Number(value)), String(name)]}
        />
        <Line
          type="monotone"
          dataKey="activeMedian"
          name="Active (median)"
          stroke="var(--muted-foreground)"
          strokeWidth={1.5}
          dot={false}
          connectNulls
        />
        <Line
          type="monotone"
          dataKey="reservedMedian"
          name="Reserved (median)"
          stroke="var(--foreground)"
          strokeWidth={1.5}
          strokeDasharray="4 3"
          dot={false}
          connectNulls
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
