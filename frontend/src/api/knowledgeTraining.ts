import { API_BASE, authFetch, iterSSEFrames } from "./client";
import { withSSETimeout } from "./sse";

export type KnowledgeTrainingMode = "random" | "high_freq";
export type KnowledgeTrainingDepth = "basic" | "understand" | "interview_expression";

export interface KnowledgeTrainingSourceRef {
  filename: string;
  header_path?: string;
}

export interface KnowledgeTrainingCard {
  id: string;
  topic: string;
  title: string;
  knowledge: string;
  example: string;
  question: string;
  answer: string;
  tags: string[];
  source_refs: KnowledgeTrainingSourceRef[];
}

export interface KnowledgeTrainingAvailabilityItem {
  name: string;
  icon?: string;
  file_count: number;
  chunk_count: number;
  available: boolean;
}

export interface KnowledgeTrainingAvailability {
  topics: Record<string, KnowledgeTrainingAvailabilityItem>;
}

export interface KnowledgeTrainingCardsPayload {
  topic: string;
  count: number;
  mode: KnowledgeTrainingMode;
  depth: KnowledgeTrainingDepth;
  seed?: string;
}

export interface KnowledgeTrainingCardsResult {
  cards: KnowledgeTrainingCard[];
  total: number;
  seed: string;
}

interface KnowledgeTrainingCallbacks {
  onProgress?: (message: string) => void;
  onCard?: (card: KnowledgeTrainingCard) => void;
}

export async function getKnowledgeTrainingAvailability(): Promise<KnowledgeTrainingAvailability> {
  const res = await authFetch(`${API_BASE}/knowledge-training/availability`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function generateKnowledgeTrainingCards(
  payload: KnowledgeTrainingCardsPayload,
  callbacks?: KnowledgeTrainingCallbacks,
): Promise<KnowledgeTrainingCardsResult> {
  return withSSETimeout(async (signal) => {
    const res = await authFetch(`${API_BASE}/knowledge-training/cards`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
    if (!res.ok) throw new Error(await res.text());

    const streamedCards: KnowledgeTrainingCard[] = [];
    let result: KnowledgeTrainingCardsResult | null = null;
    for await (const event of iterSSEFrames(res)) {
      if (event.type === "progress") callbacks?.onProgress?.(event.message);
      else if (event.type === "card") {
        streamedCards.push(event.data);
        callbacks?.onCard?.(event.data);
      } else if (event.type === "complete") {
        result = event.data;
      } else if (event.type === "error") {
        throw new Error(event.message || "训练卡片生成失败");
      }
    }

    if (result) return result;
    if (streamedCards.length > 0) {
      return { cards: streamedCards, total: streamedCards.length, seed: payload.seed || "" };
    }
    throw new Error("训练卡片生成失败：未收到结果");
  });
}
