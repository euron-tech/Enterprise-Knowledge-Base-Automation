import { Link } from "react-router-dom";

/**
 * Landing page. The thesis is the product's least common feature: it refuses.
 * The hero shows a real answer with a real citation next to a real refusal,
 * because "grounded and cited" is a claim every RAG product makes and almost
 * none demonstrate.
 */

const CAPABILITIES = [
  {
    title: "Answers only from your documents",
    body: "Every factual claim carries a citation that resolves to a real chunk of a real document. A citation the model invents is stripped before you ever see it — and if none survive, you get the refusal instead of a guess.",
  },
  {
    title: "Refuses when the evidence isn't there",
    body: "No hedging, no plausible-sounding filler. One fixed sentence, every time, so you always know the difference between “the documents say” and “the model thinks”.",
  },
  {
    title: "Department and tenant isolation",
    body: "Scope is enforced inside the vector query, not filtered out afterwards. A document you may not see returns nothing at all — its existence is never disclosed.",
  },
  {
    title: "An agent that decides how to answer",
    body: "A LangGraph planner chooses which tools to call, decomposes multi-part questions, and decides when it has enough. It plans; it never decides what it is allowed to reach.",
  },
  {
    title: "Any format, any language",
    body: "PDF, Office documents, spreadsheets, images, audio and video. Ask in your language and get an answer in it, citing sources written in another.",
  },
  {
    title: "Every request accounted for",
    body: "Tokens, cost, latency, model, cache status and a correlation id on every answer — traceable from the response header through the logs to the trace.",
  },
];

const GUARDRAILS = [
  "Direct prompt injection",
  "Instructions hidden inside uploaded documents",
  "System-prompt extraction",
  "Cross-tenant probing",
  "Retrieval poisoning",
  "Malicious filenames and file types",
  "Unsafe HTML and script content",
  "Runaway token consumption",
];

export function Landing() {
  return (
    <div className="landing">
      <header className="lp-nav">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            ◆
          </span>
          <span className="brand-name">EKBA</span>
        </div>
        <Link className="btn btn-primary btn-sm" to="/login">
          Sign in
        </Link>
      </header>

      <section className="lp-hero">
        <div className="lp-hero-copy">
          <p className="eyebrow">Enterprise Knowledge-Base Automation</p>
          <h1>
            Your policies, answered exactly — <em>or not at all.</em>
          </h1>
          <p className="lp-sub">
            Ask your department&rsquo;s handbooks, SOPs and contracts in plain language.
            Every answer is grounded in an approved document and cited. When the evidence
            isn&rsquo;t there, the system says so rather than inventing something that
            sounds right.
          </p>
          <div className="lp-cta">
            <Link className="btn btn-primary" to="/login">
              Sign in to your workspace
            </Link>
            <a className="btn btn-ghost" href="#how">
              How it works
            </a>
          </div>
        </div>

        {/* The thesis, shown rather than claimed: the same product answering and refusing. */}
        <div className="lp-demo" aria-label="Example answers">
          <div className="demo-card">
            <div className="demo-q mono">How many weeks of parental leave?</div>
            <div className="demo-a">
              Employees with 12 months of service receive 18 weeks of parental leave at
              full pay. An additional 8 weeks of unpaid leave may be requested.
            </div>
            <div className="demo-cite">
              <span className="chip">acme-hr-handbook.md</span>
              <span className="chip mono">p.1</span>
              <span className="demo-conf ok">grounded</span>
            </div>
          </div>

          <div className="demo-card demo-refusal">
            <div className="demo-q mono">What is the CFO sign-off threshold?</div>
            <div className="demo-a demo-muted">
              I could not find enough evidence in the approved documents to answer this
              question.
            </div>
            <div className="demo-cite">
              <span className="chip">0 citations</span>
              <span className="demo-conf muted">no finance grant</span>
            </div>
          </div>
        </div>
      </section>

      <section className="lp-section" id="how">
        <h2 className="lp-h2">What it does</h2>
        <div className="lp-grid">
          {CAPABILITIES.map((c) => (
            <article className="lp-card" key={c.title}>
              <h3>{c.title}</h3>
              <p>{c.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="lp-section">
        <h2 className="lp-h2">Built to be attacked</h2>
        <p className="lp-lede">
          Retrieved documents are treated as hostile input, not instructions. These are
          tested continuously against a growing attack corpus, and a guardrail is never
          weakened to make a test pass.
        </p>
        <ul className="lp-chips">
          {GUARDRAILS.map((g) => (
            <li key={g}>{g}</li>
          ))}
        </ul>
      </section>

      <section className="lp-section lp-flow">
        <h2 className="lp-h2">How a question is answered</h2>
        <ol className="flow">
          <li>
            <span className="flow-stage">Before the agent</span>
            <span className="flow-body">
              Authenticate, validate, scan for injection, check the cache. Fixed steps the
              agent cannot skip.
            </span>
          </li>
          <li>
            <span className="flow-stage">The agent</span>
            <span className="flow-body">
              Plans, calls read-only tools, gathers evidence, and judges whether it has
              enough to answer.
            </span>
          </li>
          <li>
            <span className="flow-stage">After the agent</span>
            <span className="flow-body">
              Validate every citation, run output guardrails, record cost and latency,
              emit the trace.
            </span>
          </li>
        </ol>
        <p className="lp-note">
          Autonomy sits between gates it cannot reach. An agent that could decide to skip
          authorization would be a vulnerability, not a feature.
        </p>
      </section>

      <footer className="lp-footer">
        <span className="mono">EKBA v0.1.0 · dev · ap-south-1</span>
        <Link to="/login">Sign in</Link>
      </footer>
    </div>
  );
}
