/** Typed API client. Correlation id propagated on every request. */

export interface Citation {
  chunk_id: string;
  document_id: string;
  document_name: string;
  page_number: number;
  document_version: number;
  source_uri: string;
  time_offset_ms: number | null;
}

export interface RetrievedChunk {
  chunk_id: string;
  document_id: string;
  document_name: string;
  page_number: number;
  score: number;
}

/** The frozen /chat contract. All eleven fields are always present. */
export interface ChatResponse {
  answer: string;
  citations: Citation[];
  retrieved_chunks: RetrievedChunk[];
  model_used: string;
  input_tokens: number;
  output_tokens: number;
  estimated_cost: number;
  latency_ms: number;
  cache_hit: boolean;
  trace_id: string;
  confidence: number;
  terminal_reason?: string;
  iterations?: number;
  tool_calls?: number;
}

const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

/** In memory only — never localStorage, which is readable by any injected script. */
let accessToken: string | null = null;
export function setAccessToken(token: string | null): void {
  accessToken = token;
}

function correlationId(): string {
  return crypto.randomUUID();
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  headers.set("X-Correlation-ID", correlationId());
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);

  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  const cid = res.headers.get("X-Correlation-ID") ?? "unknown";

  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body?.message) message = body.message;
    } catch {
      /* non-JSON error body; keep the generic message */
    }
    throw new Error(`${message} [${cid}]`);
  }
  return (await res.json()) as T;
}

export function askQuestion(question: string): Promise<ChatResponse> {
  // Note: no tenant_id / department / role is ever sent. The server derives scope
  // from the verified token; sending them would be rejected by extra="forbid".
  return request<ChatResponse>("/chat", {
    method: "POST",
    body: JSON.stringify({ question }),
  });
}

export function search(query: string, topK = 8) {
  return request("/search", {
    method: "POST",
    body: JSON.stringify({ query, top_k: topK }),
  });
}

export function listDocuments() {
  return request("/documents");
}
