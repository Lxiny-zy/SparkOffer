// Shared formatting + color thresholds for RAG quality metrics.
// Inputs are 0-1 fractions.
//
// Two coloring modes:
//  - Per-metric (preferred): pass a metricKey so each metric is colored against
//    ITS OWN normal/excellent band. Different metrics live on different scales —
//    e.g. query↔document relevance naturally lands ~0.45-0.65, so the old global
//    0.7-green threshold wrongly painted healthy retrieval yellow.
//  - Global fallback: no key → legacy ≥0.7 green / ≥0.4 yellow / else red. Kept
//    for call sites that pass arbitrary 0-1 values (e.g. per-chunk scores).

export interface MetricSpec {
  label: string;
  compares: string;      // what is being compared
  how: string;           // how the number is computed
  normal: [number, number]; // typical healthy band (0-1)
  excellent: number;     // at/above this = excellent (green)
}

// Keys cover both the question-gen retrieval gauges and the RAGAS benchmark.
export const METRIC_SPEC: Record<string, MetricSpec> = {
  // ── 出题阶段：零 LLM 成本健康度（非 ground-truth）──
  relevance: {
    label: "相关度",
    compares: "检索片段 ↔ 查询（薄弱点）",
    how: "每条查询取与片段的最高余弦，再求平均",
    normal: [0.45, 0.65],
    excellent: 0.7,
  },
  discrimination: {
    label: "区分度",
    compares: "最佳片段 ↔ 次佳片段",
    how: "每条查询的 (top1 − top2 余弦) 求平均；衡量检索是否真挑出了重点",
    normal: [0.05, 0.15],
    excellent: 0.2,
  },
  diversity: {
    label: "多样性",
    compares: "片段两两之间",
    how: "1 − 片段间平均余弦；低 = 检索坍缩成近重复段",
    normal: [0.3, 0.6],
    excellent: 0.5,
  },
  // ── RAGAS 基准：ground-truth 离线评测 ──
  hit_at_k: {
    label: "Hit@K",
    compares: "gold 源片段 ↔ top-K 检索结果",
    how: "gold 片段是否落在 top-K 的比例（含从同源合成的送分自命中）",
    normal: [0.6, 0.85],
    excellent: 0.85,
  },
  hit_at_k_strict: {
    label: "Hit@K (严格)",
    compares: "gold 源片段 ↔ top-K（排除自命中）",
    how: "排除「检索到 gold 自身源文」的送分项后的命中率，更接近真实泛化检索",
    normal: [0.45, 0.75],
    excellent: 0.75,
  },
  mrr: {
    label: "MRR",
    compares: "gold 片段在结果中的名次",
    how: "命中名次倒数的平均（1/rank）；越靠前越高",
    normal: [0.5, 0.8],
    excellent: 0.8,
  },
  context_precision: {
    label: "Precision",
    compares: "检索片段 ↔ 参考答案",
    how: "相关片段是否排在前面（Average Precision，含 ground truth）",
    normal: [0.6, 0.85],
    excellent: 0.85,
  },
  context_recall: {
    label: "Recall",
    compares: "参考答案各事实点 ↔ 检索上下文",
    how: "参考答案拆句后被上下文支撑的加权比例（full/partial/none）",
    normal: [0.6, 0.85],
    excellent: 0.85,
  },
  faithfulness: {
    label: "Faithfulness",
    compares: "生成答案各断言 ↔ 检索上下文",
    how: "答案拆 claim 后有上下文依据的加权比例（full/partial/none）",
    normal: [0.7, 0.9],
    excellent: 0.9,
  },
  answer_relevancy: {
    label: "Relevancy",
    compares: "原问题 ↔ 由答案反推的问题",
    how: "从答案逆向生成问题，与原问题的语义余弦；衡量答案切题度",
    normal: [0.7, 0.9],
    excellent: 0.9,
  },
  answer_correctness: {
    label: "Correctness",
    compares: "生成答案 ↔ 参考答案",
    how: "答案与参考答案的语义余弦",
    normal: [0.6, 0.85],
    excellent: 0.85,
  },
  // 生成侧（Review 页 0-100 展示，复用 faithfulness/relevancy 语义）
  answer_relevance: {
    label: "切题度",
    compares: "回答 ↔ 问题",
    how: "LLM 评分：回答是否直接切题（0-10 归一）",
    normal: [0.6, 0.85],
    excellent: 0.85,
  },
};

export function fmtPct01(v: number | null | undefined): string {
  if (v == null) return "--";
  return `${Math.round(v * 100)}%`;
}

/** Resolve a 0-1 value + optional metric key into a semantic tier. */
function metricTier(v: number, metricKey?: string): "good" | "fair" | "poor" {
  const spec = metricKey ? METRIC_SPEC[metricKey] : undefined;
  if (spec) {
    if (v >= spec.excellent) return "good";
    if (v >= spec.normal[0]) return "fair";
    return "poor";
  }
  // Global fallback.
  if (v >= 0.7) return "good";
  if (v >= 0.4) return "fair";
  return "poor";
}

/** CSS-variable color, for `style={{ color }}` call sites. */
export function metricColorVar(v: number, metricKey?: string): string {
  const tier = metricTier(v, metricKey);
  return tier === "good" ? "var(--green)" : tier === "fair" ? "var(--warning)" : "var(--red)";
}

/** Tailwind text-color class. */
export function metricColorClass(v: number, metricKey?: string): string {
  const tier = metricTier(v, metricKey);
  return tier === "good" ? "text-green-500" : tier === "fair" ? "text-yellow-500" : "text-red-500";
}

/** Tailwind background class, for progress-bar fills. */
export function metricBarClass(v: number, metricKey?: string): string {
  const tier = metricTier(v, metricKey);
  return tier === "good" ? "bg-green-500" : tier === "fair" ? "bg-yellow-500" : "bg-red-500";
}
