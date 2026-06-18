import { useState } from "react";
import { ChevronDown, ChevronUp, FileText } from "lucide-react";
import { cn } from "@/lib/utils";
import { fmtPct01, metricColorClass, metricBarClass, metricTierLabel, metricBarPct } from "@/lib/metrics";
import { MetricInfoTooltip } from "@/components/MetricInfoTooltip";
import type { RAGRetrievalMetrics } from "@/api/interview";

interface RAGMetricsPanelProps {
  metrics: RAGRetrievalMetrics;
}

const METRIC_KEYS: (keyof Pick<RAGRetrievalMetrics, "relevance" | "coverage" | "diversity">)[] = [
  "relevance",
  "coverage",
  "diversity",
];

const METRIC_LABEL: Record<string, string> = {
  relevance: "相关度",
  coverage: "覆盖度",
  diversity: "多样性",
};

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
        {METRIC_KEYS.map((key) => {
          const val = metrics[key];
          return (
            <MetricInfoTooltip key={key} metricKey={key}>
              <div className="rounded-lg bg-card/60 border border-border/40 px-3 py-2">
                <div className="text-[11px] text-muted-fg mb-1">{METRIC_LABEL[key]}</div>
                <div className="flex items-baseline gap-1.5">
                  <span className={cn("text-lg font-semibold font-mono tabular-nums", val != null ? metricColorClass(val, key) : "text-muted-fg")}>
                    {fmtPct01(val)}
                  </span>
                  {val != null && (
                    <span className={cn("text-[11px] font-medium", metricColorClass(val, key))}>
                      {metricTierLabel(val, key)}
                    </span>
                  )}
                </div>
                <div className="mt-1 h-1 rounded-full bg-muted overflow-hidden">
                  {val != null && (
                    <div
                      className={cn("h-full rounded-full transition-all duration-500", metricBarClass(val, key))}
                      style={{ width: metricBarPct(val, key) }}
                    />
                  )}
                </div>
              </div>
            </MetricInfoTooltip>
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
