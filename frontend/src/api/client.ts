/**
 * Typed API client.
 *
 * Same-origin by default: CloudFront proxies the API paths to the ALB, so the
 * browser never makes a cross-origin call and there is no mixed-content problem.
 *
 * The access token is held in memory only. localStorage is readable by any
 * injected script, so a token there is a token stolen by the first XSS.
 */

const BASE = import.meta.env.VITE_API_BASE ?? "";

// ---------------------------------------------------------------- types
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

/** The frozen /chat contract — all eleven fields, always present. */
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

export interface Identity {
  user_id: string;
  email: string;
  tenant_id: string;
  role: "user" | "admin";
  departments: string[];
  is_admin: boolean;
  permission_scope_hash: string;
}

export interface DocumentSummary {
  document_id: string;
  name: string;
  department: string;
  version: number;
  pages: number;
}

export interface SearchHit {
  chunk_id: string;
  document_id: string;
  document_name: string;
  page_number: number;
  score: number;
  text: string;
}

export interface AdminMetrics {
  tenant_id: string;
  requests: number;
  documents_active: number;
  tokens_in: number;
  tokens_out: number;
  estimated_cost: number;
  latency_p50_ms: number;
  latency_p95_ms: number;
  refusals: number;
  limit_exceeded: number;
  avg_iterations: number;
  avg_tool_calls: number;
  refusal_message: string;
}

export interface Health {
  status: string;
  checks?: Record<string, string>;
}

export interface UploadResult {
  document_id: string;
  status: string;
  chunks_written: number;
  quarantined_elements: number;
  pages: number;
  warnings: string[];
  message?: string;
}

// ---------------------------------------------------------------- token
let accessToken: string | null = null;
export function setAccessToken(token: string | null): void {
  accessToken = token;
}
export function hasToken(): boolean {
  return accessToken !== null;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly correlationId: string,
  ) {
    super(message);
  }
}

function correlationId(): string {
  return crypto.randomUUID?.() ?? String(Date.now());
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (!(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
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
      /* non-JSON body; keep the generic message */
    }
    throw new ApiError(message, res.status, cid);
  }
  return (await res.json()) as T;
}

// ---------------------------------------------------------------- endpoints
export async function login(email: string, password: string): Promise<string> {
  const body = await request<{ id_token: string }>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  setAccessToken(body.id_token);
  return body.id_token;
}

export function me(): Promise<Identity> {
  return request<Identity>("/me");
}

export function askQuestion(question: string): Promise<ChatResponse> {
  // No tenant_id / department / role is ever sent. The server derives scope from
  // the verified token, and would reject these fields anyway (extra="forbid").
  return request<ChatResponse>("/chat", {
    method: "POST",
    body: JSON.stringify({ question }),
  });
}

export function search(query: string, topK = 8) {
  return request<{ results: SearchHit[]; count: number }>("/search", {
    method: "POST",
    body: JSON.stringify({ query, top_k: topK }),
  });
}

export function listDocuments() {
  return request<{ documents: DocumentSummary[]; count: number }>("/documents");
}

export function adminMetrics(): Promise<AdminMetrics> {
  return request<AdminMetrics>("/admin/metrics");
}

export function uploadDocument(file: File, department: string): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("department", department);
  return request<UploadResult>("/documents/upload", { method: "POST", body: form });
}

export function sendFeedback(messageId: string, rating: number, reason = "") {
  return request<{ status: string }>("/feedback", {
    method: "POST",
    body: JSON.stringify({ message_id: messageId, rating, reason }),
  });
}

export async function health(): Promise<Health> {
  const res = await fetch(`${BASE}/readyz`);
  if (!res.ok) throw new Error(`health check failed (${res.status})`);
  return (await res.json()) as Health;
}
