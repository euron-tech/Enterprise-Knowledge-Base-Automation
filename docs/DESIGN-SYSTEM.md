# Design System — EKBA UI

**Source of truth: the Euron CRM at `https://crm-dev.euronsystems.com/`.** The EKBA frontend
replicates its design language so the two products feel like one suite.

Tokens below were extracted from the live production CSS bundle (`/assets/index-DXD2wZcn.css`,
95,950 bytes) on 2026-08-09 — these are the real shipped values, not an approximation.

> Note: the CRM is a client-rendered SPA behind a login, so only the token layer and the route/label
> inventory could be read from the built assets. Screen-level composition (exact spacing inside
> authenticated pages) should be confirmed visually before Phase 7 UI sign-off.

---

## 1. Stack (match it)

| Layer | CRM uses | EKBA uses |
|---|---|---|
| Build | Vite | Vite |
| Framework | React + TypeScript | React + TypeScript |
| CSS | Tailwind CSS | Tailwind CSS |
| Components | shadcn/ui on Radix primitives | same |
| Icons | lucide-react | same |
| Toasts | sonner | same |
| Routing | react-router | same |
| Client state | zustand (persisted) | zustand + TanStack Query for server state |
| Fonts | Geist Variable + Geist Mono Variable, **self-hosted woff2** | same |

Self-hosting the fonts is deliberate — no Google Fonts request, which keeps the strict CSP simple
and avoids a third-party dependency on first paint.

---

## 2. Colour tokens

The CRM uses the shadcn convention: **HSL triplets without the `hsl()` wrapper** for surface
tokens, and **space-separated RGB triplets** for semantic/brand tokens. Consume them as
`hsl(var(--token))` and `rgb(var(--token))`.

### 2.1 Light (`:root`) — copy verbatim

```css
:root {
  /* surfaces — HSL triplets */
  --background: 215 20% 97%;
  --foreground: 220 14% 11%;
  --card: 0 0% 100%;
  --card-foreground: 220 14% 11%;
  --popover: 0 0% 100%;
  --popover-foreground: 220 14% 11%;
  --secondary: 215 18% 93%;
  --secondary-foreground: 220 14% 11%;
  --muted: 215 18% 93%;
  --muted-foreground: 220 6% 42%;
  --accent: 215 18% 93%;
  --accent-foreground: 220 14% 11%;
  --destructive: 0 72% 44%;
  --destructive-foreground: 0 0% 100%;
  --border: 215 16% 85%;
  --input: 215 16% 80%;
  --radius: 0.5rem;

  /* brand + semantic — RGB triplets */
  --brand: 10 102 194;            /* #0A66C2 */
  --brand-foreground: 255 255 255;
  --success: 5 150 105;           /* #059669 */
  --warning: 217 119 6;           /* #D97706 */
  --info: 124 58 237;             /* #7C3AED */
  --danger: 220 38 38;            /* #DC2626 */
  --neutral: 100 116 139;         /* #64748B */

  /* elevation */
  --shadow-sm: 0 1px 2px 0 rgb(20 20 30 / .04);
  --shadow:    0 1px 3px 0 rgb(20 20 30 / .06), 0 1px 2px -1px rgb(20 20 30 / .05);
  --shadow-md: 0 4px 12px -2px rgb(20 20 30 / .08);
  --shadow-lg: 0 16px 40px -12px rgb(20 20 30 / .14);
}
```

### 2.2 Dark (`.dark`) — copy verbatim

```css
.dark {
  --background: 200 28% 4%;
  --foreground: 205 22% 92%;
  --card: 206 26% 9%;
  --card-foreground: 205 22% 92%;
  --popover: 206 26% 9%;
  --popover-foreground: 205 22% 92%;
  --secondary: 206 22% 15%;
  --secondary-foreground: 205 22% 92%;
  --muted: 206 20% 14%;
  --muted-foreground: 208 16% 63%;
  --accent: 206 22% 16%;
  --accent-foreground: 205 22% 92%;
  --destructive: 0 62% 50%;
  --destructive-foreground: 0 0% 100%;
  --border: 205 18% 19%;
  --input: 205 18% 23%;

  /* brand inverts to near-white on dark */
  --brand: 255 255 252 !important;
  --brand-foreground: 12 18 24 !important;

  /* semantics lighten for contrast on a near-black ground */
  --success: 52 211 153;
  --warning: 251 191 36;
  --info: 167 139 250;
  --danger: 248 113 113;
  --neutral: 148 163 184;

  --shadow-sm: 0 1px 2px 0 rgb(0 0 0 / .5);
  --shadow:    0 1px 3px 0 rgb(0 0 0 / .6), 0 1px 2px -1px rgb(0 0 0 / .5);
  --shadow-md: 0 4px 16px -2px rgb(0 0 0 / .65);
  --shadow-lg: 0 18px 44px -12px rgb(0 0 0 / .75);
}
```

Two things worth copying deliberately:

- **The dark ground is a near-black desaturated blue (`200 28% 4%`), not grey.** Cards sit at 9%
  lightness, so elevation reads as a lift rather than a border.
- **Brand inverts in dark mode.** A `#0A66C2` button on a 4%-lightness ground fails contrast, so
  dark mode flips the brand surface to near-white with dark text. Do not "fix" this back.

### 2.3 Contrast

The token pairs are AA-compliant as shipped: light `foreground`/`background` ≈ 15.9:1, dark
`foreground`/`background` ≈ 15.4:1, `muted-foreground` on `background` ≈ 5.6:1 light / 6.2:1 dark.
**Any new pairing must be checked** — in particular `--warning` on light backgrounds is
borderline for small text, so use it for icons, borders and badge fills, not body copy.

Never use colour as the only signal. Ingestion status, guardrail blocks and refusals all carry an
icon and a text label alongside the colour.

---

## 3. Multi-tenant brand override (copy this mechanism)

The CRM lets each tenant supply a brand colour, applied **before first paint** to avoid a flash:

```js
// inline in <head>, runs before the bundle loads
var hex = parsed.state.user.tenant.brand_color;      // e.g. "#0A66C2"
var n = parseInt(hex.replace('#',''), 16);
document.documentElement.style.setProperty(
  '--brand', ((n>>16)&255) + ' ' + ((n>>8)&255) + ' ' + (n&255)
);
```

EKBA is multi-tenant too, so adopt this directly: `tenants.settings_json.brand_color` drives
`--brand` per tenant.

**Security requirements for our implementation** (the CRM's version is a dev app; ours must be
hardened):

- Validate the hex strictly server-side **and** client-side with `/^#?[0-9a-fA-F]{6}$/` before it
  reaches `setProperty`. Never interpolate an unvalidated tenant string into CSS.
- The inline theme script is the one exception to our no-inline-script CSP rule. Allow it with a
  **CSP hash** (`script-src 'sha256-...'`), never with `'unsafe-inline'`.
- Enforce a minimum contrast ratio on the chosen brand colour, or fall back to the default and warn
  the admin. A tenant must not be able to make their own UI illegible.

The same pre-paint pattern applies to dark mode: read `localStorage['ekba-theme']` and add the
`dark` class before the bundle loads.

---

## 4. Typography

```css
--font-sans: "Geist Variable", "Inter Variable", ui-sans-serif, system-ui, sans-serif;
--font-mono: "Geist Mono Variable", ui-monospace, SFMono-Regular, monospace;
```

- Variable fonts, `font-weight: 100 900`, `font-display: swap`, self-hosted `.woff2` with
  `unicode-range` subsetting (latin, latin-ext, cyrillic, cyrillic-ext, vietnamese, symbols).
- **Subsetting matters for EKBA**: the product answers in any language. Ship the CRM's existing
  subsets plus whichever additional ranges the tenant base needs, and keep `font-display: swap` so
  a missing subset never blocks paint.

### Type scale (Tailwind defaults as used by the CRM)

| Role | Size / line-height | Weight | Tracking |
|---|---|---|---|
| Page title | 1.875rem / 2.25rem | 600 | -0.025em |
| Section heading | 1.5rem / 2rem | 600 | -0.02em |
| Card title | 1.125rem / 1.75rem | 600 | -0.025em |
| Body | 0.875rem / 1.25rem | 400 | normal |
| Body large | 1rem / 1.5rem | 400 | normal |
| Label / meta | 0.75rem / 1rem | 500 | 0.025em |
| Micro (badges, table meta) | 11px | 500 | 0.025em |
| Code / IDs / citations | 0.875rem mono | 400 | normal |

Headings run at 600, not 700 — the CRM's shipped weights are dominated by 500/600. Tight negative
tracking on large text, slight positive tracking on small caps-ish labels.

**Use the mono stack for**: `chunk_id`, `document_id`, `trace_id`, `correlation_id`, checksums,
token counts, and cost figures. Identifiers must be monospaced so users can compare them.

---

## 5. Shape, elevation, motion

| Token | Value | Use |
|---|---|---|
| `--radius` | `0.5rem` | Base. Cards, inputs, buttons |
| `calc(var(--radius) - 2px)` | 6px | Nested elements inside a card |
| `calc(var(--radius) - 4px)` | 4px | Badges, small chips |
| `0.75rem` / `1rem` | 12/16px | Large panels, modals |
| `9999px` | pill | Status chips, avatars, filter pills |

Elevation is the four-step shadow scale in §2 — `--shadow-sm` for resting cards, `--shadow-md` for
popovers/dropdowns, `--shadow-lg` for modals and the command palette. Do not invent new shadows.

`@media (prefers-reduced-motion: reduce)` is honoured in the CRM bundle. Match it: disable
transitions and the streaming-text animation for users who ask for less motion.

---

## 6. Responsive

Tailwind defaults, exactly as the CRM ships them:

| Breakpoint | Min width |
|---|---|
| `sm` | 640px |
| `md` | 768px |
| `lg` | 1024px |
| `xl` | 1280px |
| `2xl` | 1536px |

Observed layout widths: sidebar `16rem` expanded / `12rem` narrow / `18–20rem` wide variants;
side panels `24rem` / `28rem`; content measures `36rem`.

### EKBA responsive behaviour (required — the product must be usable on a phone)

| Range | Chat | Documents | Admin |
|---|---|---|---|
| `< 640px` | Single column. Sidebar becomes a Radix Sheet drawer. Citations collapse into an expandable block **below** the answer. Composer pinned to the bottom with safe-area inset | Card list, no table | Stacked stat cards; charts scroll horizontally in their own container |
| `640–1024px` | Two-pane: nav rail (icons, 4rem) + conversation. Citations still inline | Compact table, fewer columns | Two-column stat grid |
| `≥ 1024px` | Three-pane: sidebar 16rem + conversation + citation panel 24rem | Full table with all columns | Full dashboard grid |
| `≥ 1536px` | Content max-width capped so the answer measure stays ~36rem; citation panel widens to 28rem | — | — |

Hard rules:

- The page body never scrolls horizontally. Wide tables, code blocks and diagrams each scroll inside
  their own `overflow-x: auto` container.
- Touch targets ≥ 44×44px on coarse pointers.
- Use `dvh`, not `vh`, for full-height panels — mobile browser chrome otherwise clips the composer.
- Respect safe-area insets on notched devices.
- Test at 320px width; nothing may clip or overflow.

---

## 7. Application shell

The CRM's shell — replicate the structure, change the contents:

**Header (sticky, `h-14`, `bg-card`, `border-b`):** product mark + tenant name on the left;
global search / command palette (⌘K) centre; theme toggle, notifications, avatar menu right.

**Sidebar (`w-64`, collapsible to a `w-16` icon rail, `bg-card`, `border-r`):** grouped nav with
lucide icons; active item uses `bg-accent` with a `rgb(var(--brand))` left indicator; the sidebar
collapses to a Sheet below `lg`.

**Content:** page header (title + description + primary action), then the page body on
`bg-background`.

**Footer:** the CRM is an app shell — it has **no marketing footer**. Match that: a minimal footer
strip carrying version, environment badge, and links to status/support/privacy. Do not build a
marketing footer into an authenticated product.

### EKBA navigation

The CRM's own route inventory (`/dashboard`, `/contacts`, `/kb`, `/agents`, `/analytics`,
`/audit`, `/settings`, `/users`, `/billing`, `/integrations`…) shows the grouping convention.
EKBA's equivalent:

| Group | Items |
|---|---|
| Ask | Chat, Conversations, Saved answers |
| Knowledge | Documents, Upload, Ingestion jobs, Departments |
| Insight | Analytics, Usage & cost, Evaluations |
| Admin | Users & grants, Audit log, Prompt releases, Settings |

Admin items render only for `role === "admin"` — **as a UI convenience, not as the control.** The
server enforces authorization regardless (`frontend.md` §2).

---

## 8. EKBA-specific components

These have no CRM equivalent and must be designed to fit the token system:

| Component | Design |
|---|---|
| **Citation chip** | Inline, mono `chunk_id`, `bg-secondary`, `radius - 4px`; hover reveals document name + page/timestamp; click opens the source panel. A citation that fails to resolve renders in `--danger` with an error icon — **never silently dropped** |
| **Citation panel** | Right panel `24rem` (`28rem` at 2xl); grouped by document, showing version, page/timestamp, relevance score, and a presigned preview |
| **Refusal state** | A first-class card, not an error toast: `--neutral` border, info icon, the exact refusal string verbatim, plus "request access" and "rephrase" actions. Never styled as a failure |
| **Clarification prompt** | `--info` accented card with a single input — the agent asking one question |
| **Agent activity strip** | Coarse whitelisted steps only ("Searching documents", "Checking tables") with a subtle pulse. **Never renders the plan, tool arguments or raw tool output** |
| **Confidence meter** | Small bar using `--success` / `--warning` / `--danger` bands, with the numeric value beside it. Always paired with text — never colour alone |
| **Response metadata row** | Mono, `--muted-foreground`, 11px: `model_used` · `latency_ms` · tokens · `estimated_cost` · cache badge · `trace_id` (click to copy) |
| **Ingestion job status** | Badge set: queued `--neutral`, running `--info`, completed `--success`, quarantined `--warning`, failed `--danger`. Icon + label always |
| **Quarantine banner** | `--warning` surface on a document whose content was flagged for embedded injection, with the admin review action |

---

## 9. Accessibility

- WCAG AA contrast on every pairing; verify any new one.
- Full keyboard navigation; visible focus ring using `--brand` at 2px offset.
- Radix primitives give correct roles and focus trapping — use them rather than hand-rolling
  dialogs, menus and tooltips.
- Streaming answers announce completion to screen readers via an `aria-live="polite"` region;
  do not announce every token.
- RTL support (Arabic, Hebrew) via logical CSS properties — the product answers in any language, so
  this is a functional requirement, not a nicety.
- Honour `prefers-reduced-motion`.

---

## 10. Implementation checklist (Phase 7)

- [ ] `tailwind.config.ts` maps every token above; no raw hex in components
- [ ] `index.css` carries `:root` and `.dark` blocks verbatim from §2
- [ ] Geist + Geist Mono self-hosted as variable woff2 with unicode-range subsets
- [ ] Pre-paint inline script for theme + tenant brand, allowed by **CSP hash**
- [ ] Tenant brand hex validated on both sides; contrast floor enforced with fallback
- [ ] shadcn/ui initialised with `--radius: 0.5rem`, lucide icons, sonner toasts
- [ ] Shell: sticky header, collapsible sidebar, ⌘K command palette, minimal app footer
- [ ] Responsive verified at 320 / 640 / 768 / 1024 / 1440 / 1920 px
- [ ] No horizontal body scroll at any width; wide content scrolls in its own container
- [ ] Citation, refusal, clarification and confidence components built to §8
- [ ] Dark and light both audited for contrast
- [ ] Axe accessibility scan clean; keyboard-only walkthrough passes
