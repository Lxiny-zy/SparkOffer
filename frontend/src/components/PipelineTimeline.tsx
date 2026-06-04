import { CheckCircle2, Circle, Loader2, XCircle, AlertTriangle } from "lucide-react";
import { useMemo } from "react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

import type { PipelineStageEvent, RAGRetrievalMetrics } from "@/api/interview";

/**
 * 5 known stages — order matters for rendering. Keep in sync with
 * backend/graphs/drill_pipeline.py: STAGE_NAMES.
 */
const STAGE_ORDER = ["prepare", "retrieve", "generate", "validate", "finalize"] as const;
type StageKey = (typeof STAGE_ORDER)[number];

const STAGE_LABELS: Record<StageKey, string> = {
  prepare: "准备上下文",
  retrieve: "检索知识库",
  generate: "AI 生成题目",
  validate: "校验",
  finalize: "持久化",
};

export interface PipelineStageState {
  status: "pending" | "running" | "ok" | "error";
  duration_ms?: number;
  detail?: string;
  startedAt?: number;
  rag_metrics?: RAGRetrievalMetrics;
}

export type PipelineStages = Record<string, PipelineStageState>;

interface PipelineTimelineProps {
  stages: PipelineStages;
  /** When true, totals and bottleneck highlight are computed across all done stages. */
  totalHighlight?: boolean;
}

/**
 * Stack the 5 known pipeline stages with per-stage timing.
 * - Stages without events yet show "等待中..."
 * - Currently running stage shows a spinner
 * - The slowest finished stage is highlighted as the bottleneck so the user
 *   can spot where to optimize next.
 */
export function PipelineTimeline({ stages, totalHighlight = true }: PipelineTimelineProps) {
  const { totalMs, slowestStage } = useMemo(() => {
    let total = 0;
    let slowest: StageKey | null = null;
    let slowestMs = -1;
    for (const key of STAGE_ORDER) {
      const s = stages[key];
      if (s?.status === "ok" && typeof s.duration_ms === "number") {
        total += s.duration_ms;
        if (s.duration_ms > slowestMs) {
          slowestMs = s.duration_ms;
          slowest = key;
        }
      }
    }
    return { totalMs: total, slowestStage: slowest };
  }, [stages]);

  return (
    <div className="rounded-xl border border-border/60 bg-muted/30 backdrop-blur-sm p-3 space-y-2">
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium text-muted-fg">出题流水线 · 阶段耗时</span>
        {totalHighlight && totalMs > 0 && (
          <Badge variant="outline" className="font-mono">
            总计 {formatMs(totalMs)}
          </Badge>
        )}
      </div>

      <ol className="space-y-1.5">
        {STAGE_ORDER.map((key) => {
          const state = stages[key];
          const isBottleneck =
            slowestStage === key && state?.status === "ok" && totalMs > 0 && (state.duration_ms ?? 0) > totalMs * 0.4;
          return (
            <StageRow
              key={key}
              label={STAGE_LABELS[key]}
              state={state ?? { status: "pending" }}
              bottleneck={isBottleneck}
            />
          );
        })}
      </ol>
    </div>
  );
}

interface StageRowProps {
  label: string;
  state: PipelineStageState;
  bottleneck: boolean;
}

function StageRow({ label, state, bottleneck }: StageRowProps) {
  const isError = state.status === "error";
  const isRunning = state.status === "running";
  const isOk = state.status === "ok";
  const isPending = state.status === "pending";

  return (
    <li
      className={cn(
        "flex items-center gap-2.5 rounded-md px-2 py-1.5 text-[13px] transition-colors",
        isRunning && "bg-primary/8",
        bottleneck && "border border-orange-400/40 bg-orange-500/5",
      )}
    >
      <span className="shrink-0">
        {isPending && <Circle size={14} className="text-muted-fg/60" />}
        {isRunning && <Loader2 size={14} className="text-primary animate-spin" />}
        {isOk && <CheckCircle2 size={14} className="text-green-500" />}
        {isError && <XCircle size={14} className="text-red-500" />}
      </span>

      <span
        className={cn(
          "shrink-0 font-medium",
          isPending && "text-muted-fg/70",
          isError && "text-red-500",
        )}
      >
        {label}
      </span>

      {state.detail && (
        <span className="truncate text-[11px] text-muted-fg flex-1 min-w-0">{state.detail}</span>
      )}
      {!state.detail && <span className="flex-1" />}

      {bottleneck && (
        <Badge variant="outline" className="border-orange-400/50 text-orange-500 gap-1 text-[10px] font-mono">
          <AlertTriangle size={10} /> 瓶颈
        </Badge>
      )}

      {typeof state.duration_ms === "number" && (
        <span
          className={cn(
            "shrink-0 font-mono text-[11px] tabular-nums",
            isError ? "text-red-500" : "text-muted-fg",
            bottleneck && "text-orange-500 font-semibold",
          )}
        >
          {formatMs(state.duration_ms)}
        </span>
      )}
      {isRunning && !state.duration_ms && (
        <span className="shrink-0 font-mono text-[11px] tabular-nums text-primary/80">运行中…</span>
      )}
      {isPending && (
        <span className="shrink-0 font-mono text-[11px] tabular-nums text-muted-fg/60">等待中</span>
      )}
    </li>
  );
}

function formatMs(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)}s`;
  return `${Math.round(ms)}ms`;
}

/** Reducer-style helper: apply a server pipeline_stage event to a stages map. */
export function applyStageEvent(prev: PipelineStages, evt: PipelineStageEvent): PipelineStages {
  const next: PipelineStages = { ...prev };
  const current = next[evt.stage] ?? { status: "pending" };
  if (evt.status === "start") {
    next[evt.stage] = { ...current, status: "running", startedAt: evt.ts };
  } else if (evt.status === "ok") {
    next[evt.stage] = {
      ...current,
      status: "ok",
      duration_ms: evt.duration_ms,
      detail: evt.detail,
      rag_metrics: evt.rag_metrics,
    };
  } else if (evt.status === "error") {
    next[evt.stage] = {
      ...current,
      status: "error",
      duration_ms: evt.duration_ms,
      detail: evt.detail,
    };
  }
  return next;
}

export const PIPELINE_STAGE_ORDER = STAGE_ORDER;
