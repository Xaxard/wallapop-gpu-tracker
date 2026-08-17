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
import type { ObservationRow } from "@/lib/types";
import { compactDateTime, money } from "@/lib/format";

const STATUS_COLOR: Record<string, string> = {
  active: "var(--muted-foreground)",
  reserved: "var(--foreground)",
};

const STATUS_RADIUS: Record<string, number> = {
  active: 3,
  reserved: 4,
};

function Dot(props: { cx?: number; cy?: number; payload?: ObservationRow }) {
  const { cx, cy, payload } = props;
  if (cx === undefined || cy === undefined || !payload) return null;
  const status = payload.status ?? "active";
  return <circle cx={cx} cy={cy} r={STATUS_RADIUS[status] ?? 3} fill={STATUS_COLOR[status]} />;
}

export function ObservationTimelineChart({ observations }: { observations: ObservationRow[] }) {
  if (observations.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">
        No observations recorded.
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={192}>
      <LineChart data={observations} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
        <XAxis
          dataKey="seen_at"
          tickFormatter={(d) => compactDateTime(d)}
          tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
          axisLine={{ stroke: "var(--border)" }}
          tickLine={false}
          minTickGap={40}
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
          labelFormatter={(d) => compactDateTime(d as string)}
          formatter={(value, _name, item) => [
            `${money(Number(value))} · ${(item?.payload as ObservationRow)?.status ?? ""}`,
            "price",
          ]}
        />
        <Line
          type="stepAfter"
          dataKey="price"
          stroke="var(--muted-foreground)"
          strokeWidth={1.5}
          dot={<Dot />}
          activeDot={{ r: 4 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
