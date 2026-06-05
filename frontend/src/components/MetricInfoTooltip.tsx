import type { ReactNode } from "react";
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "@/components/ui/tooltip";
import { METRIC_SPEC, fmtPct01 } from "@/lib/metrics";

interface MetricInfoTooltipProps {
  metricKey: string;
  children: ReactNode;
  /** Fallback label when metricKey has no spec entry. */
  label?: string;
}

/** Wraps a metric value and, on hover, explains what it compares, how it's
 *  computed, and its normal / excellent bands. Reads from METRIC_SPEC so the
 *  copy stays in one place. Falls back to rendering children plainly when the
 *  key is unknown. */
export function MetricInfoTooltip({ metricKey, children, label }: MetricInfoTooltipProps) {
  const spec = METRIC_SPEC[metricKey];
  if (!spec) return <>{children}</>;

  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="cursor-help">{children}</span>
        </TooltipTrigger>
        <TooltipContent className="max-w-[260px] p-3 space-y-1.5 text-left">
          <div className="text-xs font-semibold text-foreground">{label || spec.label}</div>
          <div className="text-[11px] text-muted-fg leading-relaxed">
            <span className="text-foreground/80">比较：</span>{spec.compares}
          </div>
          <div className="text-[11px] text-muted-fg leading-relaxed">
            <span className="text-foreground/80">算法：</span>{spec.how}
          </div>
          <div className="flex items-center gap-3 pt-0.5 text-[11px]">
            <span className="text-muted-fg">
              正常 <span className="font-mono" style={{ color: "var(--warning)" }}>
                {fmtPct01(spec.normal[0])}–{fmtPct01(spec.normal[1])}
              </span>
            </span>
            <span className="text-muted-fg">
              优秀 <span className="font-mono" style={{ color: "var(--green)" }}>
                ≥{fmtPct01(spec.excellent)}
              </span>
            </span>
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
