import { useMemo } from "react";
import { ScoreHistoryEntry } from "../../types/api";

const WEEKS = 26;
const DAYS = 7;
const DAY_LABELS = ["", "一", "", "三", "", "五", ""];

/** Local YYYY-MM-DD. toISOString() is UTC — east of UTC it keys cells one day
 *  behind the server-provided local date strings in `history`. */
function localDateKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function getColor(count: number): string {
  if (count === 0) return "var(--border)";
  if (count === 1) return "color-mix(in srgb, var(--sig-accent) 38%, transparent)";
  if (count === 2) return "color-mix(in srgb, var(--sig-accent) 68%, transparent)";
  return "var(--sig-accent)";
}

interface CellData {
  date: string;
  count: number;
  avgScore: number | null;
}

interface LearningHeatmapProps {
  history: ScoreHistoryEntry[] | undefined;
}

export default function LearningHeatmap({ history }: LearningHeatmapProps) {
  const { grid, months } = useMemo(() => {
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    const dayMap = new Map<string, { count: number; totalScore: number }>();
    (history || []).forEach((h) => {
      if (!h.date) return;
      const key = h.date.slice(0, 10);
      const existing = dayMap.get(key) || { count: 0, totalScore: 0 };
      existing.count++;
      if (h.avg_score) existing.totalScore += h.avg_score;
      dayMap.set(key, existing);
    });

    const endDay = new Date(today);
    const endDow = endDay.getDay();
    const startDay = new Date(endDay);
    startDay.setDate(startDay.getDate() - (WEEKS * 7 - 1) - endDow);

    const cells: CellData[][] = [];
    const monthLabels: { label: string; col: number }[] = [];
    let lastMonth = -1;

    for (let w = 0; w < WEEKS; w++) {
      const week: CellData[] = [];
      for (let d = 0; d < DAYS; d++) {
        const current = new Date(startDay);
        current.setDate(startDay.getDate() + w * 7 + d);
        const key = localDateKey(current);
        const info = dayMap.get(key);
        const isFuture = current > today;
        week.push({
          date: key,
          count: isFuture ? -1 : (info?.count || 0),
          avgScore: info && info.count > 0 ? Math.round(info.totalScore / info.count * 10) / 10 : null,
        });
        if (d === 0 && current.getMonth() !== lastMonth) {
          lastMonth = current.getMonth();
          monthLabels.push({
            label: `${current.getMonth() + 1}月`,
            col: w,
          });
        }
      }
      cells.push(week);
    }

    return { grid: cells, months: monthLabels };
  }, [history]);

  if (!history || history.length === 0) return null;

  return (
    <div className="overflow-x-auto">
      <div className="inline-flex gap-0.5 min-w-fit">
        <div className="flex flex-col gap-0.5 mr-1 pt-5">
          {DAY_LABELS.map((label, i) => (
            <div key={i} className="h-[13px] text-[10px] text-dim leading-[13px] text-right pr-0.5">
              {label}
            </div>
          ))}
        </div>
        <div>
          <div className="flex gap-0.5 mb-1 h-4">
            {grid.map((_, w) => {
              const m = months.find((m) => m.col === w);
              return (
                <div key={w} className="w-[13px] text-[10px] text-dim text-center">
                  {m?.label || ""}
                </div>
              );
            })}
          </div>
          <div className="flex gap-0.5">
            {grid.map((week, w) => (
              <div key={w} className="flex flex-col gap-0.5">
                {week.map((cell, d) => (
                  <div
                    key={d}
                    className="w-[13px] h-[13px] rounded-sm transition-colors group relative"
                    style={{
                      backgroundColor: cell.count < 0 ? "transparent" : getColor(cell.count),
                      opacity: cell.count < 0 ? 0.2 : 1,
                    }}
                  >
                    {cell.count >= 0 && (
                      <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 hidden group-hover:block z-50">
                        <div className="bg-card border border-border rounded-lg px-2.5 py-1.5 shadow-lg text-xs whitespace-nowrap">
                          <div className="font-medium">{cell.date}</div>
                          <div className="text-dim mt-0.5">
                            {cell.count > 0
                              ? `${cell.count} 次训练${cell.avgScore != null ? ` · 均分 ${cell.avgScore}` : ""}`
                              : "无训练"}
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>
      </div>
      <div className="flex items-center gap-1.5 mt-2 text-[10px] text-dim justify-end">
        <span>少</span>
        {[0, 1, 2, 3].map((level) => (
          <div
            key={level}
            className="w-[11px] h-[11px] rounded-sm"
            style={{ backgroundColor: getColor(level) }}
          />
        ))}
        <span>多</span>
      </div>
    </div>
  );
}
