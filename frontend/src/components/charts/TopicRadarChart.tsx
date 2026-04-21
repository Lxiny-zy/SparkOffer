import {
  ResponsiveContainer, RadarChart, Radar, PolarGrid,
  PolarAngleAxis, PolarRadiusAxis, Tooltip,
} from "recharts";
import { TopicMastery } from "../../types/api";

interface CustomTooltipProps {
  active?: boolean;
  payload?: any[];
}

function CustomTooltip({ active, payload }: CustomTooltipProps) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="bg-card border border-border rounded-lg px-3 py-2 shadow-lg text-sm">
      <div className="font-medium">{d.topic}</div>
      <div className="font-bold text-primary mt-0.5">{d.score}/100</div>
      {d.notes && <div className="text-xs text-dim mt-0.5">{d.notes}</div>}
    </div>
  );
}

interface TopicRadarChartProps {
  mastery: Record<string, TopicMastery> | undefined;
}

export default function TopicRadarChart({ mastery }: TopicRadarChartProps) {
  if (!mastery || Object.keys(mastery).length === 0) return null;

  const data = Object.entries(mastery).map(([topic, info]) => ({
    topic,
    score: info.score ?? (info.level ? info.level * 20 : 0),
    notes: info.notes || "",
  }));

  if (data.length < 3) {
    return (
      <div className="flex items-end gap-2 h-[200px] px-4">
        {data.map((d) => (
          <div key={d.topic} className="flex-1 flex flex-col items-center gap-1">
            <div className="text-xs font-bold text-primary">{d.score}</div>
            <div
              className="w-full rounded-t-lg bg-gradient-to-t from-primary/60 to-primary transition-all duration-500"
              style={{ height: `${Math.max(d.score * 1.6, 8)}px` }}
            />
            <div className="text-xs text-dim text-center truncate w-full">{d.topic}</div>
          </div>
        ))}
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={260}>
      <RadarChart data={data} cx="50%" cy="50%" outerRadius="72%">
        <PolarGrid stroke="var(--border)" />
        <PolarAngleAxis
          dataKey="topic"
          tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
        />
        <PolarRadiusAxis
          domain={[0, 100]}
          tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
          tickCount={5}
        />
        <Tooltip content={<CustomTooltip />} />
        <Radar
          dataKey="score"
          stroke="var(--primary)"
          fill="var(--primary)"
          fillOpacity={0.2}
          strokeWidth={2}
        />
      </RadarChart>
    </ResponsiveContainer>
  );
}
