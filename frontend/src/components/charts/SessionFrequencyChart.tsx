import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis,
  CartesianGrid, Tooltip,
} from "recharts";
import { ScoreHistoryEntry } from "../../types/api";

interface WeekData {
  week: string;
  count: number;
  label: string;
}

function aggregateByWeek(history: ScoreHistoryEntry[]): WeekData[] {
  const weeks: Record<string, WeekData> = {};
  for (const h of history) {
    if (!h.date) continue;
    const d = new Date(h.date);
    const day = d.getDay();
    const monday = new Date(d);
    monday.setDate(d.getDate() - ((day + 6) % 7));
    const key = monday.toISOString().slice(0, 10);
    if (!weeks[key]) weeks[key] = { week: key, count: 0, label: `${monday.getMonth() + 1}/${monday.getDate()}` };
    weeks[key].count++;
  }
  return Object.values(weeks).sort((a, b) => a.week.localeCompare(b.week)).slice(-12);
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: any[];
}

function CustomTooltip({ active, payload }: CustomTooltipProps) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="bg-card border border-border rounded-lg px-3 py-2 shadow-lg text-sm">
      <div className="text-dim text-xs">周起始 {d.week}</div>
      <div className="font-bold text-primary mt-0.5">{d.count} 次训练</div>
    </div>
  );
}

interface SessionFrequencyChartProps {
  history: ScoreHistoryEntry[] | undefined;
}

export default function SessionFrequencyChart({ history }: SessionFrequencyChartProps) {
  if (!history || history.length < 2) return null;

  const data = aggregateByWeek(history);
  if (data.length < 2) return null;

  return (
    <ResponsiveContainer width="100%" height={180}>
      <BarChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: -8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
        <XAxis
          dataKey="label"
          tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
          tickLine={false}
          axisLine={{ stroke: "var(--border)" }}
        />
        <YAxis
          allowDecimals={false}
          tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
          tickLine={false}
          axisLine={{ stroke: "var(--border)" }}
        />
        <Tooltip content={<CustomTooltip />} />
        <Bar
          dataKey="count"
          fill="var(--primary)"
          radius={[4, 4, 0, 0]}
          maxBarSize={32}
          fillOpacity={0.8}
        />
      </BarChart>
    </ResponsiveContainer>
  );
}
