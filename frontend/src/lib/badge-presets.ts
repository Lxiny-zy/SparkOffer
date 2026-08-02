/**
 * Centralized semantic color presets — used by Badge variants, score pills,
 * mode chips, health dots. Keeps the visual language consistent across pages.
 */

export type BadgeVariantKey =
  | "default"
  | "secondary"
  | "destructive"
  | "outline"
  | "success"
  | "blue"
  | "warning";

// ── Interview mode → badge variant + display label ──
export const MODE_BADGES: Record<string, { text: string; variant: BadgeVariantKey }> = {
  resume:      { text: "简历面试",  variant: "default" },
  topic_drill: { text: "专项训练",  variant: "success" },
  jd_prep:     { text: "JD 备面",   variant: "blue" },
  job_prep:    { text: "JD 备面",   variant: "blue" },
  recording:   { text: "录音复盘",  variant: "warning" },
};

export function getModeBadge(mode: string): { text: string; variant: BadgeVariantKey } {
  return MODE_BADGES[mode] ?? { text: mode || "未知", variant: "secondary" };
}

// ── Score → 4-level color band ──
export interface ScoreBand {
  bg: string;
  color: string;
  label: string;
  level: "excellent" | "good" | "fair" | "poor" | "empty";
}

export function getScoreBand(score: number | null | undefined): ScoreBand {
  // bg derives from the same token as color via color-mix — theme-aware in both
  // light and dark sig (the old hardcoded light-theme rgba() tints were not).
  // This is the single source of truth for score coloring; page-local
  // getScoreColor/scoreToColor reimplementations should migrate here.
  if (score == null) {
    return { bg: "color-mix(in srgb, var(--muted-fg) 10%, transparent)", color: "var(--muted-fg)", label: "--", level: "empty" };
  }
  if (score >= 8) return { bg: "color-mix(in srgb, var(--green) 15%, transparent)",   color: "var(--green)",   label: "卓越", level: "excellent" };
  if (score >= 6) return { bg: "color-mix(in srgb, var(--tertiary) 15%, transparent)", color: "var(--tertiary)", label: "良好", level: "good" };
  if (score >= 4) return { bg: "color-mix(in srgb, var(--warning) 18%, transparent)",  color: "var(--warning)",  label: "待提升", level: "fair" };
  return            { bg: "color-mix(in srgb, var(--red) 15%, transparent)",      color: "var(--red)",      label: "需重练", level: "poor" };
}

// ── Channel health → dot color + tooltip ──
export interface HealthInfo {
  className: string;
  pulse: boolean;
  label: string;
}

export function getHealthInfo(health: { healthy?: boolean; error_count?: number } | undefined): HealthInfo {
  // Token-based utilities (bg-red/bg-orange/bg-green → --color-red/orange/green
  // → sig status tokens), not the native *-500 scale that ignores the theme.
  if (!health)              return { className: "bg-dim/30",  pulse: false, label: "未知" };
  if (!health.healthy)      return { className: "bg-red",     pulse: true,  label: "冷却中" };
  if ((health.error_count ?? 0) > 0)
                            return { className: "bg-orange",  pulse: false, label: `${health.error_count} 次错误` };
  return                            { className: "bg-green",  pulse: false, label: "健康" };
}

// ── Reasoning effort → estimated latency hint ──
export const REASONING_EFFORT_HINT: Record<string, { label: string; latency: string; tone: string }> = {
  "":        { label: "Off",     latency: "默认速度",  tone: "text-dim" },
  minimal:   { label: "Minimal", latency: "≈ +1s",    tone: "text-info" },
  low:       { label: "Low",     latency: "≈ +5s",    tone: "text-info" },
  medium:    { label: "Medium",  latency: "≈ +15s",   tone: "text-warning" },
  high:      { label: "High",    latency: "≈ +30s",   tone: "text-orange" },
  xhigh:     { label: "XHigh",   latency: "≈ +60s",   tone: "text-orange" },
};
