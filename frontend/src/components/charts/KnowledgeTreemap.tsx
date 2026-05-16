import { ResponsiveContainer, Treemap, Tooltip } from "recharts";
import { TopicMastery } from "../../types/api";

function scoreToColor(score: number): string {
  if (score >= 70) return "#22c55e";
  if (score >= 50) return "#eab308";
  if (score >= 30) return "#f97316";
  return "#ef4444";
}

interface TreemapCellProps {
  x: number;
  y: number;
  width: number;
  height: number;
  name: string;
  score: number;
}

function CustomCell({ x, y, width, height, name, score }: TreemapCellProps) {
  if (width < 4 || height < 4) return null;
  const color = scoreToColor(score);
  const showText = width > 50 && height > 30;
  const showScore = width > 40 && height > 20;

  return (
    <g>
      <rect
        x={x}
        y={y}
        width={width}
        height={height}
        rx={4}
        fill={color}
        fillOpacity={0.75}
        stroke="var(--card)"
        strokeWidth={2}
      />
      {showText && (
        <text
          x={x + width / 2}
          y={y + height / 2 - (showScore ? 6 : 0)}
          textAnchor="middle"
          dominantBaseline="central"
          fill="white"
          fontSize={Math.min(13, width / name.length * 1.2)}
          fontWeight={600}
        >
          {name.length > width / 8 ? name.slice(0, Math.floor(width / 8)) + ".." : name}
        </text>
      )}
      {showScore && showText && (
        <text
          x={x + width / 2}
          y={y + height / 2 + 12}
          textAnchor="middle"
          dominantBaseline="central"
          fill="rgba(255,255,255,0.85)"
          fontSize={11}
        >
          {score}分
        </text>
      )}
    </g>
  );
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: any[];
}

function TreemapTooltip({ active, payload }: CustomTooltipProps) {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload;
  if (!d) return null;
  return (
    <div className="bg-card border border-border rounded-lg px-3 py-2 shadow-lg text-sm">
      <div className="font-medium">{d.name}</div>
      <div className="flex items-center gap-2 mt-1">
        <div
          className="w-2.5 h-2.5 rounded-sm"
          style={{ backgroundColor: scoreToColor(d.score) }}
        />
        <span className="font-bold">{d.score}/100</span>
      </div>
      {d.notes && <div className="text-xs text-dim mt-1">{d.notes}</div>}
    </div>
  );
}

interface KnowledgeTreemapProps {
  mastery: Record<string, TopicMastery> | undefined;
}

export default function KnowledgeTreemap({ mastery }: KnowledgeTreemapProps) {
  if (!mastery || Object.keys(mastery).length === 0) return null;

  const data = Object.entries(mastery).map(([name, info]) => ({
    name,
    size: Math.max(info.score ?? (info.level ? info.level * 20 : 10), 10),
    score: info.score ?? (info.level ? info.level * 20 : 0),
    notes: info.notes || "",
  }));

  if (data.length < 2) return null;

  return (
    <div>
      <ResponsiveContainer width="100%" height={200}>
        <Treemap
          data={data}
          dataKey="size"
          aspectRatio={4 / 3}
          content={<CustomCell x={0} y={0} width={0} height={0} name="" score={0} />}
          isAnimationActive={true}
          animationDuration={600}
        >
          <Tooltip content={<TreemapTooltip />} />
        </Treemap>
      </ResponsiveContainer>
      <div className="flex items-center gap-2 mt-2 text-[10px] text-dim justify-end">
        <span>低</span>
        {[10, 30, 50, 70, 90].map((s) => (
          <div
            key={s}
            className="w-3 h-3 rounded-sm"
            style={{ backgroundColor: scoreToColor(s), opacity: 0.75 }}
          />
        ))}
        <span>高</span>
      </div>
    </div>
  );
}
