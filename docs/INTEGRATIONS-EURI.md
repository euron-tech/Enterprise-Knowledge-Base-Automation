# Euri AI Gateway — Verified Integration Contract

**Status: probed against the live API on 2026-08-09 and confirmed working.** Every statement below
is backed by an actual request/response, not by documentation reading. Re-verify with
`scripts/probe_euri.py` after any gateway change.

> **No API key appears in this document, in any file in this repository, or in any log.** The key
> is read at runtime from AWS Secrets Manager at `ekba/<env>/euri-api-key`.
> The sample key shared during development is considered **compromised** (it was transmitted in
> plaintext chat) and must be revoked and replaced before any deployment.

---

## 1. Endpoint summary

| Property | Value |
|---|---|
| Base URL | `https://api.euron.one/api/v1/euri` |
| Auth | `Authorization: Bearer <key>` |
| Content type | `application/json` |
| Wire format | OpenAI-compatible (Chat Completions + Embeddings) |
| Edge | Cloudflare → AWS ALB (`AWSALB` cookie, `cf-cache-status` header) |

| Endpoint | Method | Verified |
|---|---|---|
| `/models` | GET | ✅ returns model list **with pricing** |
| `/embeddings` | POST | ✅ text only |
| `/chat/completions` | POST | ✅ incl. tools, vision, streaming, JSON modes |

---

## 2. Models

`GET /models` returns each model with a `pricing` object — **this is the source of truth for cost
recording.** Do not hard-code prices; fetch and cache this list.

```json
{"id": "gpt-4.1-nano", "owned_by": "openai", "premium": false,
 "pricing": {"type": "per_token", "inputPriceUsdPerMillion": 0.1, "outputPriceUsdPerMillion": 0.4}}
```

Confirmed available (non-exhaustive): `gpt-4.1`, `gpt-4.1-mini` ($0.40 / $1.60 per 1M),
`gpt-4.1-nano` ($0.10 / $0.40 per 1M), `gpt-5.4-mini`, `gemini-2.5-flash`, `claude-sonnet-4-6`,
`llama-4-scout-17b-16e-instruct`, `gemini-embedding-2-preview`.

**Project defaults:** generation `gpt-4.1`, embeddings `gemini-embedding-2-preview`.
Routing tiers: `gpt-4.1-nano` (planner/cheap classification) → `gpt-4.1-mini` (routine answers) →
`gpt-4.1` (complex synthesis). Fallback chain configured, never hard-coded per call site.

---

## 3. Embeddings — `POST /embeddings`

### Verified request

```json
{"model": "gemini-embedding-2-preview",
 "input": "Annual leave accrues at 2 days per month.",
 "dimensions": 768}
```

| Behaviour | Result |
|---|---|
| String input | ✅ 200, **3072 dimensions** by default |
| List input (batching) | ✅ 200, one vector per item — use this, it is ~1 call per chunk batch |
| `dimensions: 768` | ✅ 200, returns exactly 768 dims |
| Latency | 0.8–1.8 s observed |

### ⚠️ Finding 1 — the embedding model is TEXT-ONLY

**This resolves open question #2 and changes the ingestion design.** Every multimodal input shape
was rejected:

| Attempted `input` shape | Result |
|---|---|
| `{"text": "..."}` | ❌ HTTP 500 — `400 Request contains an invalid argument` |
| `{"image_url": {"url": "data:image/png;base64,..."}}` | ❌ HTTP 500 — same |
| `[{"type":"text",...},{"type":"image_url",...}]` | ❌ HTTP 500 — same |
| `{"image": "<b64>", "mime_type": "image/png"}` | ❌ HTTP 500 — same |

Despite the published schema typing `input` as an object, **only a string or a list of strings
works.** There is no multimodal embedding path on this gateway today.

**Consequence — the ingestion pipeline must bridge modalities into text before embedding:**

```
image / diagram  → chat vision (gpt-4.1) → description text → embed text
audio            → transcription          → timestamped text → embed text
video            → keyframes → vision descriptions
                 + audio → transcript      → embed text, linked by timestamp
tables           → Markdown serialization  → embed text
```

The original raw asset still lives in S3 and is still cited; only the *retrievable* representation
is text. Every derived description is stored so citations resolve to the real asset and page or
timestamp.

### ⚠️ Finding 2 — embedding usage reporting is unusable for cost

Every embedding response returns the same usage regardless of input size:

| Input | Reported usage |
|---|---|
| 6 words | `{"prompt_tokens": 0, "total_tokens": 500}` |
| ~2,000 words | `{"prompt_tokens": 0, "total_tokens": 500}` |
| ~20,000 words | `{"prompt_tokens": 0, "total_tokens": 500}` |

`prompt_tokens` is always 0 and `total_tokens` is a flat 500. **Do not derive embedding cost from
the gateway response.** Count tokens client-side (tiktoken or the model's tokenizer) and apply the
price from `/models`.

### ⚠️ Finding 3 — no error on oversized input (silent truncation risk)

A ~20,000-word input returned HTTP 200 rather than an error. The gateway does not reject oversize
input, which means it is almost certainly **silently truncating** it. A silently truncated embedding
is a silently wrong embedding, and it will degrade retrieval invisibly.

**The chunker must enforce its own hard token ceiling before calling the gateway.** Never rely on
the API to reject an over-long chunk. Log and alarm on any chunk approaching the limit.

---

## 4. Chat completions — `POST /chat/completions`

OpenAI-compatible. All of the following are **verified working**:

| Capability | Status | Notes |
|---|---|---|
| Basic completion | ✅ | `usage` here **is** accurate — `prompt_tokens`, `completion_tokens`, `total_tokens` |
| `tools` + `tool_choice: "auto"` | ✅ | Returns standard `tool_calls` with `id`, `function.name`, `function.arguments` |
| **Parallel tool calls** | ✅ | Two tools returned in a single response — the agent can fan out in one turn |
| Tool result round-trip | ✅ | `role: "tool"` + `tool_call_id` accepted; model answered citing the injected chunk id |
| Forced `tool_choice` | ✅ | Specific function forced successfully |
| Vision (`image_url` content part) | ✅ | Data URIs accepted — this is the image→text bridge for ingestion |
| Streaming (`stream: true`) | ✅ | `text/event-stream`, standard `chat.completion.chunk` SSE frames |
| `response_format: json_object` | ✅ | |
| `response_format: json_schema` (strict) | ✅ | Accepted; still validate client-side |
| `temperature`, `max_tokens`, `seed`, `top_p`, `stop`, `n` | ✅ | Standard semantics |

**Tool calling working is what makes the agentic design viable on this gateway.** Parallel tool
calls in particular let the planner issue `table_lookup` and `semantic_search` in one turn instead
of burning two iterations.

### ⚠️ Finding 4 — `finish_reason` is unreliable when `tool_choice` is forced

With a forced `tool_choice`, the response contained `tool_calls` but reported
`finish_reason: "stop"` rather than `"tool_calls"`.

**The agent must branch on the presence of `message.tool_calls`, never on `finish_reason`.**

---

## 5. Error semantics

| Condition | HTTP | Body |
|---|---|---|
| Invalid/inactive key | **401** | `{"error":{"message":"Invalid or inactive API key","type":"authentication_error","code":"invalid_api_key"}}` |
| Unknown model | **400** | `{"error":{...,"type":"invalid_request_error","code":"invalid_parameters"}}` |
| Bad embedding input shape | **500** | `{"error":{"message":"400 Request contains an invalid argument.","type":"internal_error","code":"internal_server_error"}}` |

### ⚠️ Finding 5 — upstream 4xx is surfaced as HTTP 500

A malformed embedding request produced **HTTP 500** whose message body plainly says `400`. The
gateway wraps upstream client errors as server errors.

**Consequence for the client's retry policy:** a 500 from this gateway is *not* reliably transient.
Blindly retrying 500s will burn budget on a permanently invalid request.

```
if status == 500 and "400" in error.message:  → treat as PERMANENT, do not retry, raise typed error
if status == 500 otherwise:                   → retry, bounded, jittered backoff
if status == 429:                             → retry with backoff, count as rate_limited
if status in (401, 403):                      → fail immediately, alarm, never retry
if status == 400:                             → permanent, do not retry
```

---

## 6. Client requirements (`backend/app/clients/euri.py`)

- One typed client. No route or tool calls the gateway directly.
- Key read from Secrets Manager at `ekba/<env>/euri-api-key`. Never from a file, never logged.
- Explicit timeouts: connect 5 s, read 60 s (120 s for streaming).
- Bounded retries with jittered backoff, honouring the 500-is-not-transient rule above.
- Circuit breaker per model; on trip, route to the fallback model and emit a metric.
- **Client-side token counting for embeddings**; gateway `usage` used only for chat.
- Model prices fetched from `/models`, cached with a TTL, refreshed on unknown model.
- Every call emits a LangSmith span and records into `request_usage`: model, input tokens, output
  tokens, estimated cost, latency, cache status, route selected, fallback usage, tenant, prompt
  version, trace id.
- Hard input-size guard before every embedding call (Finding 3).
- Branch on `tool_calls` presence, not `finish_reason` (Finding 4).

---

## 7. Configuration

| Setting | Value |
|---|---|
| `EURI_BASE_URL` | `https://api.euron.one/api/v1/euri` |
| `EURI_API_KEY` | Secrets Manager `ekba/<env>/euri-api-key` |
| `EURI_EMBEDDING_MODEL` | `gemini-embedding-2-preview` |
| `EURI_EMBEDDING_DIMENSIONS` | `1536` (recommended — see below) |
| `EURI_GENERATION_MODEL` | `gpt-4.1` |
| `EURI_PLANNER_MODEL` | `gpt-4.1-mini` |
| `EURI_FALLBACK_MODEL` | `gpt-4.1-mini` |
| `EURI_VISION_MODEL` | `gpt-4.1` |

**Dimension recommendation.** The default 3072 dims costs ~12 KB per vector before quantization —
at 10M chunks that is ~120 GB of vector memory in Qdrant. 1536 halves it and 768 quarters it, both
verified working via the `dimensions` parameter. Recommend **1536** as the default, benchmarked
against 3072 on the eval set in Phase 6 before locking it in. **The value must be fixed before the
first production ingest** — changing it later requires a full re-embed of the corpus.

---

## 8. Security notes

- The gateway returns sensible security headers (HSTS, `nosniff`, `X-Frame-Options: DENY`,
  `referrer-policy: no-referrer`).
- It sets an `AWSALB` cookie; our client must **not** persist or forward cookies between tenants.
  Use a stateless client (no shared cookie jar).
- Treat all gateway output as untrusted content — it passes through the same injection scanning and
  output guardrails as any other text (`ai-guardrails.md`).
- Egress to `api.euron.one` is the only model-provider destination permitted by the
  NetworkPolicy allow-list.

---

## 9. Re-verification

`scripts/probe_euri.py` (Phase 1) re-runs every check in this document and diffs against the
recorded contract. Run it when: onboarding an environment, rotating the key, changing a model, or
seeing unexplained ingestion or generation failures. It reads the key from the environment, prints
**no** secret values, and makes no writes.
