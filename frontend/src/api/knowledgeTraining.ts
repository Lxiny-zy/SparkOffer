import { API_BASE, authFetch, iterSSEFrames } from "./client";
import { SSETerminalError, withSSETimeout } from "./sse";

export type KnowledgeTrainingMode = "random" | "high_freq";
export type KnowledgeTrainingDepth = "basic" | "understand" | "interview_expression";

export interface KnowledgeTrainingSourceRef {
  filename: string;
  header_path?: string;
}

export type Familiarity = "known" | "uncertain" | "unknown";

export interface KnowledgeTrainingCard {
  id: string;
  topic: string;
  title: string;
  knowledge: string;
  example: string;
  question: string;
  answer: string;
  // Optional memory hook (口诀/类比) — absent or empty when the LLM skipped it.
  mnemonic?: string;
  tags: string[];
  source_refs: KnowledgeTrainingSourceRef[];
  // Present on persisted/reviewed cards (absent on freshly-streamed ones).
  familiarity?: Familiarity | "";
  next_review?: string | null;
  review_count?: number;
}

export interface KnowledgeTrainingAvailabilityItem {
  name: string;
  icon?: string;
  file_count: number;
  chunk_count: number;
  available: boolean;
  due_count?: number;
  saved_count?: number;
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
    let sawDone = false;
    let errorMessage: string | null = null;
    for await (const event of iterSSEFrames(res)) {
      if (event.type === "progress") callbacks?.onProgress?.(event.message);
      else if (event.type === "card") {
        streamedCards.push(event.data);
        callbacks?.onCard?.(event.data);
      } else if (event.type === "complete") {
        result = event.data;
      } else if (event.type === "error") {
        errorMessage = event.message || "训练卡片生成失败";
      } else if (event.type === "done") {
        sawDone = true;
        if (event.terminal === "error" && !errorMessage) errorMessage = "训练卡片生成失败";
      }
    }

    if (errorMessage) throw new SSETerminalError(errorMessage, "error");
    if (!sawDone) throw new SSETerminalError("训练卡片连接提前关闭，未收到完成事件，请重试");
    if (result) return result;
    if (streamedCards.length > 0) {
      return { cards: streamedCards, total: streamedCards.length, seed: payload.seed || "" };
    }
    throw new Error("训练卡片生成失败：未收到结果");
  });
}

export async function getDueKnowledgeCards(topic?: string): Promise<{ cards: KnowledgeTrainingCard[]; total: number }> {
  const qs = topic ? `?topic=${encodeURIComponent(topic)}` : "";
  const res = await authFetch(`${API_BASE}/knowledge-training/due${qs}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getSavedKnowledgeCards(
  topic?: string,
  familiarity?: Familiarity,
): Promise<{ items: KnowledgeTrainingCard[]; total: number }> {
  const params = new URLSearchParams();
  if (topic) params.set("topic", topic);
  if (familiarity) params.set("familiarity", familiarity);
  const qs = params.toString() ? `?${params.toString()}` : "";
  const res = await authFetch(`${API_BASE}/knowledge-training/cards/saved${qs}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function reviewKnowledgeCard(
  cardId: string,
  familiarity: Familiarity,
): Promise<{ card: KnowledgeTrainingCard }> {
  const res = await authFetch(`${API_BASE}/knowledge-training/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ card_id: cardId, familiarity }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
