import { useEffect, useState } from "react";
import { adminMetrics, type AdminMetrics } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";

function money(n: number): string {
  return n < 0.01 ? `$${n.toFixed(6)}` : `$${n.toFixed(4)}`;
}

export function Admin() {
  const { identity } = useAuth();
  const [m, setM] = useState<AdminMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    adminMetrics()
      .then(setM)
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load metrics"))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="page"><div className="card muted">Loading…</div></div>;
  if (error || !m) {
    return (
      <div className="page">
        <div className="card error" role="alert">{error ?? "No metrics"}</div>
      </div>
    );
  }

  const answered = m.requests - m.refusals;
  const refusalRate = m.requests ? Math.round((m.refusals / m.requests) * 100) : 0;

  return (
    <div className="page">
      <div className="page-head">
        <h1>Operations</h1>
        <p className="page-sub">
          Scoped to <span className="mono">{m.tenant_id}</span>. There is no cross-tenant
          view — seniority widens departments, never tenants.
        </p>
      </div>

      <div className="stat-grid">
        <div className="stat">
          <div className="stat-v">{m.requests}</div>
          <div className="stat-l">requests</div>
        </div>
        <div className="stat">
          <div className="stat-v">{m.documents_active}</div>
          <div className="stat-l">active documents</div>
        </div>
        <div className="stat">
          <div className="stat-v">{m.latency_p50_ms}<span className="unit">ms</span></div>
          <div className="stat-l">latency p50</div>
        </div>
        <div className="stat">
          <div className="stat-v">{m.latency_p95_ms}<span className="unit">ms</span></div>
          <div className="stat-l">latency p95</div>
        </div>
        <div className="stat">
          <div className="stat-v">{money(m.estimated_cost)}</div>
          <div className="stat-l">estimated cost</div>
        </div>
        <div className="stat">
          <div className="stat-v">{(m.tokens_in + m.tokens_out).toLocaleString()}</div>
          <div className="stat-l">tokens total</div>
        </div>
      </div>

      <section className="card">
        <h2 className="section-title">Answer outcomes</h2>
        <p className="hint">
          A refusal is a correct outcome, not a fault. A rate near zero can mean the
          corpus covers everything asked — or that grounding is too permissive.
        </p>
        <div className="bar" role="img" aria-label={`${answered} answered, ${m.refusals} refused`}>
          <span
            className="bar-seg answered"
            style={{ width: `${m.requests ? (answered / m.requests) * 100 : 0}%` }}
          />
          <span
            className="bar-seg refused"
            style={{ width: `${refusalRate}%` }}
          />
        </div>
        <div className="bar-legend">
          <span><i className="dot answered" /> answered {answered}</span>
          <span><i className="dot refused" /> refused {m.refusals}</span>
          <span><i className="dot limit" /> limit exceeded {m.limit_exceeded}</span>
        </div>
      </section>

      <section className="card">
        <h2 className="section-title">Agent behaviour</h2>
        <div className="kv">
          <div><span>Average planner iterations</span><b className="mono">{m.avg_iterations}</b></div>
          <div><span>Average tool calls per request</span><b className="mono">{m.avg_tool_calls}</b></div>
          <div><span>Input tokens</span><b className="mono">{m.tokens_in.toLocaleString()}</b></div>
          <div><span>Output tokens</span><b className="mono">{m.tokens_out.toLocaleString()}</b></div>
        </div>
      </section>

      <section className="card">
        <h2 className="section-title">Refusal contract</h2>
        <p className="hint">
          Byte-exact and served from a single constant. If this string ever drifts, the
          evaluation suite fails.
        </p>
        <p className="refusal-quote">{m.refusal_message}</p>
      </section>

      <section className="card">
        <h2 className="section-title">Signed in as</h2>
        <div className="kv">
          <div><span>Email</span><b className="mono">{identity?.email}</b></div>
          <div><span>Role</span><b className="mono">{identity?.role}</b></div>
          <div><span>Departments</span><b className="mono">{identity?.departments.join(", ")}</b></div>
        </div>
      </section>
    </div>
  );
}
