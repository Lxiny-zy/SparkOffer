import { API_BASE, authFetch } from "./client";

export async function getAIConfig(): Promise<any> {
  const res = await authFetch(`${API_BASE}/settings/ai`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function saveAIConfig(config: any): Promise<any> {
  const res = await authFetch(`${API_BASE}/settings/ai`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function testLLM(params: any): Promise<any> {
  const res = await authFetch(`${API_BASE}/settings/ai/test/llm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  return res.json();
}

export async function testEmbedding(params: any): Promise<any> {
  const res = await authFetch(`${API_BASE}/settings/ai/test/embedding`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  return res.json();
}

export async function getMe(): Promise<any> {
  const res = await authFetch(`${API_BASE}/auth/me`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function updateProfile(data: any): Promise<any> {
  const res = await authFetch(`${API_BASE}/auth/profile`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function changePassword(data: { old_password: string; new_password: string }): Promise<any> {
  const res = await authFetch(`${API_BASE}/auth/password`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// ── Multi-Channel APIs ──

export async function getChannels(): Promise<any> {
  const res = await authFetch(`${API_BASE}/settings/ai/channels`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function saveChannels(config: any): Promise<any> {
  const res = await authFetch(`${API_BASE}/settings/ai/channels`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function testChannel(section: string, channel: any): Promise<any> {
  const res = await authFetch(`${API_BASE}/settings/ai/channels/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ section, channel }),
  });
  return res.json();
}

export async function getChannelsHealth(): Promise<any> {
  const res = await authFetch(`${API_BASE}/settings/ai/channels/health`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// ── Runtime tuning (context budget + retrieval) ──

export async function getTuning(): Promise<any> {
  const res = await authFetch(`${API_BASE}/settings/tuning`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function saveTuning(config: any): Promise<any> {
  const res = await authFetch(`${API_BASE}/settings/tuning`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
