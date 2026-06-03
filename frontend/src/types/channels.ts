export type ReasoningEffort = "" | "minimal" | "low" | "medium" | "high";
export type ChannelTier = "small" | "large";

export interface LLMChannel {
  id: string;
  name: string;
  api_base: string;
  keys: string[];
  model: string;
  temperature: number;
  reasoning_effort?: ReasoningEffort;
  tier?: ChannelTier;
  priority: number;
  enabled: boolean;
  proxy?: string;
}

export interface EmbeddingChannel {
  id: string;
  name: string;
  backend: "api" | "local";
  api_base: string;
  keys: string[];
  api_model: string;
  local_model: string;
  local_path: string;
  priority: number;
  enabled: boolean;
  proxy?: string;
}

export interface ASRChannel {
  id: string;
  name: string;
  keys: string[];
  model: string;
  priority: number;
  enabled: boolean;
  proxy?: string;
}

export interface RerankerChannel {
  id: string;
  name: string;
  api_base: string;
  keys: string[];
  api_model: string;
  priority: number;
  enabled: boolean;
  proxy?: string;
}

export interface ChannelHealth {
  id: string;
  name: string;
  healthy: boolean;
  error_count: number;
  cooldown_until: number | null;
  current_key_index: number;
}

export interface SectionChannels<T> {
  channels: T[];
  health: ChannelHealth[];
}

export interface AllChannelsResponse {
  llm: SectionChannels<LLMChannel>;
  embedding: SectionChannels<EmbeddingChannel>;
  asr: SectionChannels<ASRChannel>;
  reranker: SectionChannels<RerankerChannel>;
}
