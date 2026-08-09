# Rule — Frontend (React + TypeScript)

Applies to everything under `frontend/`.

**Read [`docs/DESIGN-SYSTEM.md`](../../docs/DESIGN-SYSTEM.md) before writing any component.** It
carries the exact colour, typography, radius, shadow and breakpoint tokens extracted from the Euron
CRM, which this UI must match.

---

## 1. Stack and tooling

- React 18+, TypeScript **strict**, Vite.
- **Tailwind CSS + shadcn/ui on Radix primitives, lucide-react icons, sonner toasts** — matching the
  CRM. Do not introduce a second component library or styling system.
- State: TanStack Query for server state; zustand only where genuinely shared client state exists.
- Forms: React Hook Form + Zod, with Zod schemas mirroring the backend Pydantic contracts.
- Fonts: **self-hosted** Geist Variable + Geist Mono Variable woff2 with unicode-range subsets.
  No Google Fonts request — it would complicate the CSP and add a first-paint dependency.
- Tests: Vitest + Testing Library for units, Playwright for e2e.
- ESLint + Prettier clean before done. `tsc --noEmit` clean. No `any`, no `@ts-ignore` without an
  inline reason.

## 1a. Token discipline

- **Never write a raw hex, rgb or hsl value in a component.** Everything comes from a token:
  `hsl(var(--background))`, `rgb(var(--brand))`, `var(--shadow-md)`, `var(--radius)`.
- Never invent a new shadow, radius or breakpoint. The scales in `DESIGN-SYSTEM.md` are complete.
- Both themes are first-class. Every new surface is checked in light **and** dark before it ships;
  a colour defined only inside a `.dark` block (or only outside one) is a bug.
- The brand token inverts in dark mode by design (near-white on the near-black ground). Do not
  "correct" it back to blue.

---

## 2. Security rules (the ones that actually matter here)

| Rule | Why |
|---|---|
| Access tokens live in memory, never `localStorage`/`sessionStorage` | XSS token theft |
| Refresh via a secure, http-only cookie or the Cognito SDK's storage — never hand-rolled | Same |
| **Never** `dangerouslySetInnerHTML` on answers, citations, document content or filenames | Model and document output is untrusted |
| Render markdown through a sanitizer with a strict allow-list; no raw HTML, no `javascript:` URLs | Indirect XSS from documents |
| Never render a raw `source_uri`; always render a presigned URL fetched from the API on demand | URL leakage and TTL control |
| Never construct authorization state client-side | The UI reflects permissions; it does not grant them |
| Never send `tenant_id`, `department`, `role` or `owner_id` as request parameters | The server derives them from the JWT |
| Propagate `X-Correlation-ID` on every request and surface it in error UI | Supportability |
| No secrets or API keys in frontend code or the build | Everything shipped is public |
| CSP-compatible code: no inline event handlers, no `eval`, no dynamic script injection | The page ships with a strict CSP |
| The pre-paint theme/brand script is the **only** inline script, allowed by CSP **hash** — never `unsafe-inline` | One hashed exception is auditable; a blanket allowance is not |
| Validate the tenant brand hex with `/^#?[0-9a-fA-F]{6}$/` before `setProperty` | An unvalidated tenant string interpolated into CSS is an injection sink |

The UI hides what a user cannot access, but hiding is cosmetic. The server is the authority, and
frontend code must never assume otherwise.

---

## 3. Rendering the agent's answers

- **Citations are first-class.** Every answer renders its citations with document name, version and
  page or timestamp, linked to a presigned preview. A citation that fails to resolve renders as a
  visible error — never silently dropped, never quietly rendered as plain text.
- **The refusal is rendered as a first-class state**, not an error toast. When the API returns the
  refusal string, show it plainly with a suggestion to rephrase or request access. Do not dress it
  up, do not retry automatically, do not substitute a generic "something went wrong".
- **Clarification requests** render as a prompt for one answer, not as a failed request.
- **Agent progress**, if shown, displays only coarse, safe steps ("searching documents", "checking
  tables") derived from a whitelisted status field. **Never render the plan, tool arguments, raw
  tool output, or the reasoning trace** — that is internal telemetry, not user content.
- Show `confidence`, `cache_hit`, `model_used` and `latency_ms` where useful, honestly. Never
  fabricate a confidence bar the API did not return.
- Long answers stream; citations appear only after the API has validated them.

---

## 4. Components and structure

```
frontend/src/
  api/          typed client, error mapping, correlation id
  auth/         Cognito integration, session, route guards
  features/
    chat/ documents/ admin/ feedback/
  components/   shared presentational components
  hooks/  lib/  types/  i18n/
```

- Feature-first organization. Shared code moves to `components/` only on the second use.
- One component, one responsibility. Extract when a file passes ~200 lines.
- Props typed explicitly; no `React.FC` with implicit children.
- API types are generated from or manually mirrored against the OpenAPI schema, and drift is caught
  by a test.

---

## 5. Data fetching

- All server state through TanStack Query — no `useEffect` fetch loops.
- Every query has a stable key that **includes the tenant/user scope**, so a user switch cannot
  serve another user's cached data. Clear the cache on logout.
- Handle every state: loading, empty, error, refusal, partial. "Empty" and "refused" are different
  and must look different.
- Retries: bounded, never on 4xx, never on a refusal.
- Uploads: show progress and job status; poll the ingestion job, with backoff.

---

## 6. Internationalization and accessibility

- All user-facing strings through i18n. No hard-coded copy in components.
- The refusal string is rendered from the API response verbatim — it is not an i18n key and is not
  translated client-side.
- RTL layout support (Arabic, Hebrew); logical CSS properties.
- Keyboard navigable; visible focus; correct ARIA roles; labelled controls.
- Colour contrast meets WCAG AA. Never colour as the only signal.
- Announce streaming answer completion to screen readers.

---

## 7. Performance

- Route-level code splitting; lazy-load the admin bundle.
- Virtualize long document and message lists.
- Memoize deliberately, not reflexively.
- Keep the initial bundle lean; check bundle size in CI.

---

## 8. Testing

- Unit-test components with meaningful assertions (rendered behaviour, not snapshots of markup).
- Playwright e2e must cover: login → ask → cited answer → click citation → source opens;
  unauthorized department → refusal rendered with zero chunks; upload → job status → document
  appears; admin metrics visible to admin and 403 for a user.
- Test the refusal path and the clarification path explicitly — they are product behaviour, not
  edge cases.
- Mock the API at the network boundary (MSW), not by stubbing internal modules.

---

## 9. Forbidden

| Never | Instead |
|---|---|
| `dangerouslySetInnerHTML` | Sanitized markdown renderer |
| Tokens in `localStorage` | In-memory + secure cookie refresh |
| Client-side authorization logic as the control | Server enforces; UI reflects |
| Rendering the agent's plan, tool args or raw tool output | Whitelisted coarse status only |
| Swallowing a refusal into a generic error | A first-class refusal state |
| Sending principal fields to the API | Let the server derive them |
| `any`, `@ts-ignore` without a reason | Fix the type |
| Hard-coded strings | i18n |
