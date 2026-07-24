import {
  ResponsiveContainer, RadarChart, Radar, PolarGrid,
  PolarAngleAxis, PolarRadiusAxis, Tooltip,
} from "recharts";
import { ArrowRight } from "lucide-react";
import { TopicMastery } from "../../types/api";

interface CustomTooltipProps {
  active?: boolean;
  payload?: any[];
}

function CustomTooltip({ active, payload }: CustomTooltipProps) {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload;
  if (!d) return null;
  const current = d.score;
  const prev = d.prev;
  const diff = prev != null ? current - prev : null;
  return (
    <div className="bg-card border border-border rounded-lg px-3 py-2 shadow-lg text-sm">
      <div className="font-medium">{d.topic}</div>
      <div className="font-bold text-primary mt-0.5">
        {prev != null ? (
          <span className="inline-flex items-center gap-1">{prev} <ArrowRight size={12} aria-hidden="true" /> {current} <span className={diff! > 0 ? "text-green" : diff! < 0 ? "text-red" : "text-dim"}>({diff! > 0 ? "+" : ""}{diff})</span></span>
        ) : (
          <>{current}/100</>
        )}
      </div>
      {d.notes && <div className="text-xs text-dim mt-0.5">{d.notes}</div>}
    </div>
  );
}

interface TopicRadarChartProps {
  mastery: Record<string, TopicMastery> | undefined;
  previousMastery?: Record<string, TopicMastery>;
}

export default function TopicRadarChart({ mastery, previousMastery }: TopicRadarChartProps) {
  if (!mastery || Object.keys(mastery).length === 0) return null;

  const data = Object.entries(mastery).map(([topic, info]) => ({
    topic,
    score: info.score ?? (info.level ? info.level * 20 : 0),
    prev: previousMastery?.[topic]?.score ?? null,
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

  const hasPrev = previousMastery && data.some((d) => d.prev != null);

  return (
    <ResponsiveContainer width="100%" height={280}>
      <RadarChart data={data} cx="50%" cy="50%" outerRadius="72%">
        <defs>
          <linearGradient id="radarGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--primary)" stopOpacity={0.4} />
            <stop offset="100%" stopColor="var(--primary)" stopOpacity={0.05} />
          </linearGradient>
        </defs>
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
        {hasPrev && (
          <Radar
            dataKey="prev"
            stroke="var(--muted-foreground)"
            fill="none"
            strokeWidth={1.5}
            strokeDasharray="4 3"
            isAnimationActive={true}
            animationDuration={600}
          />
        )}
        <Radar
          dataKey="score"
          stroke="var(--primary)"
          fill="url(#radarGradient)"
          strokeWidth={2.5}
          isAnimationActive={true}
          animationDuration={800}
          dot={{ r: 3, fill: "var(--primary)", stroke: "var(--card)", strokeWidth: 2 }}
        />
      </RadarChart>
    </ResponsiveContainer>
  );
}
