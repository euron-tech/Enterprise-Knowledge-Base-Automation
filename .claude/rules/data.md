# Rule — Data (PostgreSQL, Qdrant, S3, Redis)

Applies to models, migrations, repositories, vector payloads, object keys, cache entries and
retention. Read with `security.md`.

---

## 1. Tenancy is a data-layer property

- Every tenant-scoped table has `tenant_id NOT NULL`, indexed leading on `tenant_id`.
- Every repository method takes a `Principal` (or an explicit `tenant_id`), never an unscoped id.
  A method that can be called without tenant context is a defect.
- Every Qdrant query goes through the chokepoint filter builder. Hand-built filters are banned.
- Row-level security is enabled on Supabase tables where supported, pinned to the JWT claim, as
  defence in depth — not as the only defence.
- Cross-tenant joins do not exist. There is no legitimate one in v1.

---

## 2. PostgreSQL

### Schema rules

- UUIDv7 (or UUIDv4) primary keys, not sequential integers.
- `created_at`/`updated_at` as `timestamptz`, always UTC, defaulted in the database.
- Foreign keys with explicit `ON DELETE` behaviour — chosen deliberately, never defaulted by
  accident. Prefer `RESTRICT` for anything auditable.
- Enums for closed sets (`role`, `job_state`, `document_status`, `cache_status`, `terminal_reason`)
  as native enums or check constraints, matching the Python `Literal`/`Enum`.
- Money and cost as `numeric`, never float. Token counts as `bigint`.
- JSONB for genuinely variable data only (`settings_json`, `metadata_json`, `citations_json`),
  with a documented shape. Never as a way to avoid designing a column.
- Unique constraints where uniqueness matters: `(tenant_id, checksum, department)` on documents,
  `(name, version)` on prompt releases.
- `audit_events` is append-only: no update or delete path in any repository, and the application DB
  role lacks those grants.

### Tables (all ten are required)

`tenants`, `users`, `documents`, `ingestion_jobs`, `conversations`, `messages`, `request_usage`,
`user_feedback`, `prompt_releases`, `audit_events` — columns per `ARCHITECTURE.md` §4.1.

`request_usage` must carry: model, input tokens, output tokens, estimated cost, latency, cache
status, route selected, fallback usage, tenant, prompt version, trace id — plus the agent fields:
iterations, tool-call count, terminal reason, agent version.

### Migrations (Alembic)

- One migration per change, reviewed, with a working `downgrade`.
- **Expand/contract only**: add nullable → backfill → switch reads/writes → drop in a later
  release. Never drop a column in the same release the code stops using it.
- Never a destructive migration during a blue/green shift — both versions run simultaneously.
- Backfills are batched and resumable; never a single unbounded `UPDATE` on a large table.
- Migrations are tested up **and** down in CI against a seeded database.
- Never edit an applied migration; add a new one.
- Never run `alembic downgrade` against a shared environment without user approval.

### Query rules

- Parameterized queries or the ORM. No string interpolation, ever.
- No `SELECT *` in application code.
- Pagination on every list endpoint; keyset pagination for large sets.
- Index before you need it: every filter and sort column used by an endpoint.
- `EXPLAIN` anything that touches `request_usage`, `messages` or `audit_events` at scale.
- Connection pool sized to the pod count × pool size < the database's connection limit. Pressure is
  an alarm, not a surprise.

---

## 3. Qdrant

- One collection per environment: `ekba_chunks_{env}`. Never a collection per tenant in v1 (payload
  filtering with an index is the isolation mechanism, and it is tested).
- Payload indexes on `tenant_id`, `department`, `status`, `document_id`.
- **Mandatory payload fields, never null:** `document_id`, `chunk_id`, `document_name`,
  `page_number`, `source_uri`, `owner_id`, `tenant_id`, `document_version`, `checksum`,
  `created_at`. Plus `department`, `modality`, `element_type`, `status`, `lang`, and
  `time_offset_ms` for a/v.
- A write missing any mandatory field is rejected by the client wrapper, not just by review.
- `chunk_id` is deterministic: `sha256(document_id || document_version || ordinal)`.
- Upserts are idempotent. Re-ingesting identical content writes nothing new.
- Version retirement: set `status=retired` first (immediately excluded from queries), purge later.
  Never delete a live version's points as the first step.
- **Never delete a collection.** Recreating an index is a documented, approved operation, not a
  casual fix.
- Snapshots/backups scheduled; restore drilled in Phase 11.

---

## 4. S3

```
s3://ekba-{env}-documents/{tenant_id}/{department}/{document_id}/v{version}/{filename}
s3://ekba-{env}-derived/{tenant_id}/{document_id}/v{version}/{element_id}.{ext}
```

- Tenant id is the first path segment — it makes IAM prefix scoping and audit trivially readable.
- Filenames are sanitized before use: normalized unicode, stripped control characters, no traversal,
  length-bounded. The original name is preserved in the database, not in the key.
- SSE-KMS, versioning on, Block Public Access on, TLS-only policy, access logging.
- Raw uploads are immutable — a new version means a new key, never an overwrite.
- Presigned URLs: authorization-checked before issuance, TTL ≤ 5 minutes, never logged, never placed
  into the model's context.
- Deletion is lifecycle-driven after the retention window, and only for objects belonging to a
  deleted document. Never `aws s3 rm --recursive` against a bucket prefix by hand.

---

## 5. Redis (ElastiCache)

- Key namespaces: `cache:answer:*`, `rl:*`, `lock:*`, `kbver:*`. Never bare keys.
- **Every cache key includes the permission scope hash and the tenant id.** A cache entry can never
  cross a permission boundary.
- Full answer-cache key: normalized question, `tenant_id`, permission-scope hash, `kb_version`,
  prompt version, agent version, tool-registry hash, model, temperature, top-k, relevance threshold.
- TTL on every key. No unbounded growth. `maxmemory-policy` set deliberately.
- Redis is a cache and a rate-limit store — never a system of record. Losing it must degrade
  performance, never correctness.
- Never `FLUSHALL` against a shared environment.

---

## 6. Data integrity and lifecycle

- `checksum` (SHA-256 of the raw object) is the idempotency key for ingestion.
- Document deletion is a saga with a defined order: audit event → Qdrant points removed → derived
  objects marked → S3 lifecycle → database row status. Each step is idempotent and resumable.
- Orphan sweeps (vectors without a document, derived objects without a version) run as a scheduled
  job that **reports** first and only deletes project-owned, provably orphaned artifacts.
- Retention per data class is configured, documented, and enforced by lifecycle rules — not by ad
  hoc scripts.

---

## 7. PII and privacy

- Documents may contain personal data. Treat extracted text and embeddings as equally sensitive to
  the source.
- Never log document content, extracted text, embeddings or presigned URLs.
- Deletion requests remove content, derived artifacts and vectors while preserving the audit trail
  (which records the action, not the content).
- Per-tenant PII policy drives the output redaction level.

---

## 8. Forbidden

| Never | Why |
|---|---|
| A repository method without tenant scope | The cross-tenant bug waiting to happen |
| A hand-built Qdrant filter | Bypasses the only isolation mechanism |
| A Qdrant write missing a mandatory payload field | Unciteable, untraceable data |
| String-interpolated SQL | Injection |
| A destructive migration in a blue/green release | Both versions are live |
| Deleting a Qdrant collection or an S3 bucket | See `00-root.md` §1 |
| `FLUSHALL` / unbounded keys / TTL-less cache entries | Outage and stale-permission risk |
| Treating Redis as a source of truth | Data loss on eviction |
| Logging document content or embeddings | Privacy breach |
