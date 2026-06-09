import { useState } from "react";
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, Legend,
} from "recharts";
import { ScoreHistoryEntry } from "../../types/api";

const DIMENSIONS: Record<string, { label: string; color: string }> = {
  technical_depth: { label: "技术深度", color: "var(--sig-chart-1)" },
  communication: { label: "沟通表达", color: "var(--sig-chart-3)" },
  problem_solving: { label: "解题思路", color: "var(--sig-chart-4)" },
  project_articulation: { label: "项目表达", color: "var(--sig-chart-5)" },
  role_fit: { label: "岗位匹配", color: "var(--sig-chart-6)" },
  engineering_quality: { label: "工程质量", color: "var(--sig-chart-2)" },
  project_relevance: { label: "项目相关", color: "var(--sig-warning)" },
};

interface DimensionTrendChartProps {
  history: ScoreHistoryEntry[] | undefined;
}

export default function DimensionTrendChart({ history }: DimensionTrendChartProps) {
  const entries = (history || []).filter((h) => h.dimension_scores && Object.keys(h.dimension_scores).length > 0);
  if (entries.length < 2) return null;

  const allDims = new Set<string>();
  entries.forEach((e) => {
    Object.keys(e.dimension_scores!).forEach((k) => allDims.add(k));
  });
  const dims = [...allDims].filter((d) => DIMENSIONS[d]);

  const [visible, setVisible] = useState<Set<string>>(new Set(dims));

  const data = entries.map((e, i) => ({
    index: i,
    label: e.date?.slice(5, 10) || `#${i + 1}`,
    ...e.dimension_scores,
  }));

  const toggle = (dim: string) => {
    setVisible((prev) => {
      const next = new Set(prev);
      if (next.has(dim)) next.delete(dim);
      else next.add(dim);
      return next;
    });
  };

  return (
    <div>
      <div className="flex flex-wrap gap-2 mb-3">
        {dims.map((dim) => {
          const info = DIMENSIONS[dim];
          const active = visible.has(dim);
          return (
            <button
              key={dim}
              onClick={() => toggle(dim)}
              className={`text-xs px-2.5 py-1 rounded border transition-all cursor-pointer ${
                active
                  ? "border-transparent text-white"
                  : "border-border text-dim bg-transparent opacity-50"
              }`}
              style={active ? { backgroundColor: info.color } : {}}
            >
              {info.label}
            </button>
          );
        })}
      </div>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: -8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis
            dataKey="label"
            tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: "var(--border)" }}
          />
          <YAxis
            domain={[0, 10]}
            tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: "var(--border)" }}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "var(--card)",
              border: "1px solid var(--border)",
              borderRadius: "8px",
              fontSize: "12px",
            }}
            formatter={(value: number, name: string) => [
              value,
              DIMENSIONS[name]?.label || name,
            ]}
          />
          {dims
            .filter((d) => visible.has(d))
            .map((dim) => (
              <Line
                key={dim}
                type="monotone"
                dataKey={dim}
                stroke={DIMENSIONS[dim].color}
                strokeWidth={2}
                dot={{ r: 3, fill: DIMENSIONS[dim].color, stroke: "var(--card)", strokeWidth: 2 }}
                activeDot={{ r: 5 }}
                connectNulls
                isAnimationActive={true}
                animationDuration={600}
              />
            ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
