import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, Legend,
} from "recharts";
import { ScoreHistoryEntry } from "../../types/api";

const MODE_COLORS: Record<string, string> = {
  resume: "var(--sig-chart-1)",
  topic_drill: "var(--sig-chart-3)",
  jd_prep: "var(--sig-chart-2)",
  recording: "var(--sig-chart-6)",
};

const MODE_LABELS: Record<string, string> = {
  resume: "简历面试",
  topic_drill: "专项训练",
  jd_prep: "JD 备面",
  recording: "录音复盘",
};

interface CustomTooltipProps {
  active?: boolean;
  payload?: any[];
}

function CustomTooltip({ active, payload }: CustomTooltipProps) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="bg-card border border-border rounded-lg px-3 py-2 shadow-lg text-sm">
      <div className="font-medium">{d.date?.slice(0, 10)}</div>
      <div className="text-dim text-xs mt-0.5">
        {MODE_LABELS[d.mode] || d.mode}{d.topic ? ` · ${d.topic}` : ""}
      </div>
      <div className="font-bold mt-1" style={{ color: MODE_COLORS[d.mode] || "var(--primary)" }}>
        {d.avg_score}/10
      </div>
    </div>
  );
}

interface ScoreTrendChartProps {
  history: ScoreHistoryEntry[] | undefined;
}

export default function ScoreTrendChart({ history }: ScoreTrendChartProps) {
  if (!history || history.length < 2) return null;

  const data = history.map((h, i) => ({
    ...h,
    index: i,
    label: h.date?.slice(5, 10) || "",
  }));

  const modes = [...new Set(data.map((d) => d.mode))];

  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: -8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
        <XAxis
          dataKey="label"
          tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
          tickLine={false}
          axisLine={{ stroke: "var(--border)" }}
        />
        <YAxis
          domain={[0, 10]}
          tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
          tickLine={false}
          axisLine={{ stroke: "var(--border)" }}
        />
        <Tooltip content={<CustomTooltip />} />
        {modes.length > 1 && (
          <Legend
            formatter={(value: string) => MODE_LABELS[value] || value}
            wrapperStyle={{ fontSize: 12 }}
          />
        )}
        {modes.map((mode) => (
          <Line
            key={mode}
            type="monotone"
            dataKey="avg_score"
            data={data.filter((d) => d.mode === mode)}
            stroke={MODE_COLORS[mode] || "var(--primary)"}
            strokeWidth={2.5}
            dot={{ r: 4, fill: MODE_COLORS[mode] || "var(--primary)", strokeWidth: 2, stroke: "var(--card)" }}
            activeDot={{ r: 6 }}
            name={mode}
            connectNulls
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
