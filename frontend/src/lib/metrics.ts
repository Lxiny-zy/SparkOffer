// Shared formatting + color thresholds for RAG quality metrics.
// Inputs are 0-1 fractions. Thresholds: ≥0.7 good (green), ≥0.4 fair
// (warning/yellow), else poor (red). Keep these in one place so the panel,
// dashboard, and review badges never drift apart.

export function fmtPct01(v: number | null | undefined): string {
  if (v == null) return "--";
  return `${Math.round(v * 100)}%`;
}

/** CSS-variable color, for `style={{ color }}` call sites. */
export function metricColorVar(v: number): string {
  if (v >= 0.7) return "var(--green)";
  if (v >= 0.4) return "var(--warning)";
  return "var(--red)";
}

/** Tailwind text-color class. */
export function metricColorClass(v: number): string {
  if (v >= 0.7) return "text-green-500";
  if (v >= 0.4) return "text-yellow-500";
  return "text-red-500";
}

/** Tailwind background class, for progress-bar fills. */
export function metricBarClass(v: number): string {
  if (v >= 0.7) return "bg-green-500";
  if (v >= 0.4) return "bg-yellow-500";
  return "bg-red-500";
}
