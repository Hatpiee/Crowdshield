"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

interface Snapshot {
  timestamp_seconds: number;
  risk_score: number;
}

export default function RiskTrendChart({ data }: { data: Snapshot[] }) {
  if (data.length === 0) {
    return (
      <p className="py-16 text-center font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">
        No data yet
      </p>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="#1F2937" strokeDasharray="3 3" />
        <XAxis
          dataKey="timestamp_seconds"
          stroke="#94A3B8"
          fontSize={11}
          tickFormatter={(value: number) => `${value.toFixed(0)}s`}
        />
        <YAxis stroke="#94A3B8" fontSize={11} domain={[0, 100]} />
        <Tooltip
          contentStyle={{ background: "#0F1620", border: "1px solid #1F2937", fontSize: 12 }}
          labelStyle={{ color: "#94A3B8" }}
          labelFormatter={(value) => `t=${Number(value).toFixed(2)}s`}
        />
        {/* Single consistent accent line (documented judgment call): a
            per-segment value-based color gradient is possible with
            recharts but adds real complexity for a first cut — a
            uniform teal line, matching the established accent palette,
            is clear and sufficient here. */}
        <Line
          type="monotone"
          dataKey="risk_score"
          stroke="#2DD4BF"
          strokeWidth={2}
          dot={false}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
