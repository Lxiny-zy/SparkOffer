import { useState } from "react";
import { ChevronDown, ChevronUp, FileText } from "lucide-react";
import { cn } from "@/lib/utils";
import { fmtPct01, metricColorClass, metricBarClass } from "@/lib/metrics";
import type { RAGRetrievalMetrics } from "@/api/interview";

interface RAGMetricsPanelProps {
  metrics: RAGRetrievalMetrics;
}

const METRIC_LABELS: { key: keyof Pick<RAGRetrievalMetrics, "context_relevance" | "context_precision" | "context_recall">; label: string; tip: string }[] = [
  { key: "context_relevance", label: "相关度", tip: "检索 chunk 与查询的语义相关性" },
  { key: "context_precision", label: "排序质量", tip: "相关 chunk 是否排在前面" },
  { key: "context_recall", label: "覆盖率", tip: "薄弱点被 chunk 覆盖的比例（无薄弱点时不适用）" },
];

export function RAGMetricsPanel({ metrics }: RAGMetricsPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const chunks = metrics.chunk_details ?? [];

  return (
    <div className="rounded-md border border-border/60 bg-muted/30 p-3 space-y-2">
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium text-muted-fg">RAG 检索质量</span>
        {chunks.length > 0 && (
          <button
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-1 text-[11px] text-muted-fg hover:text-foreground transition-colors"
          >
            {chunks.length} 个片段
            {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          </button>
        )}
      </div>

      <div className="grid grid-cols-3 gap-2">
        {METRIC_LABELS.map(({ key, label, tip }) => {
          const val = metrics[key];
          return (
            <div
              key={key}
              className="rounded-lg bg-card/60 border border-border/40 px-3 py-2"
              title={tip}
            >
              <div className="text-[11px] text-muted-fg mb-1">{label}</div>
              <div className={cn("text-lg font-semibold font-mono tabular-nums", val != null ? metricColorClass(val) : "text-muted-fg")}>
                {fmtPct01(val)}
              </div>
              <div className="mt-1 h-1 rounded-full bg-muted overflow-hidden">
                {val != null && (
                  <div
                    className={cn("h-full rounded-full transition-all duration-500", metricBarClass(val))}
                    style={{ width: fmtPct01(val) }}
                  />
                )}
              </div>
            </div>
          );
        })}
      </div>

      {expanded && chunks.length > 0 && (
        <div className="space-y-1 pt-1">
          {chunks.map((chunk, i) => (
            <div
              key={i}
              className="flex items-center gap-2 text-[11px] px-2 py-1 rounded bg-card/40 border border-border/30"
            >
              <span className={cn("font-mono tabular-nums shrink-0 w-12 text-right", metricColorClass(chunk.score))}>
                {fmtPct01(chunk.score)}
              </span>
              <FileText size={11} className="text-muted-fg shrink-0" />
              <span className="truncate text-muted-fg" title={chunk.source}>
                {chunk.source || "unknown"}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
