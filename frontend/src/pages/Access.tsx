import { useAuth } from "../auth/AuthContext";

/**
 * Makes RBAC legible. Everything here comes from /me — that is, from the server's
 * database — so what this page shows is exactly what the retrieval filter enforces.
 * It is a mirror of the decision, not a second copy of it.
 */
export function Access() {
  const { identity } = useAuth();
  if (!identity) return null;

  return (
    <div className="page">
      <div className="page-head">
        <h1>My access</h1>
        <p className="page-sub">
          These values come from the server, not from your browser. The same values build
          the filter applied to every search you run.
        </p>
      </div>

      <section className="card">
        <h2 className="section-title">Identity</h2>
        <div className="kv">
          <div><span>Email</span><b className="mono">{identity.email}</b></div>
          <div><span>User id</span><b className="mono">{identity.user_id}</b></div>
          <div><span>Tenant</span><b className="mono">{identity.tenant_id}</b></div>
          <div>
            <span>Role</span>
            <b>
              <span className={`role-pill ${identity.is_admin ? "admin" : ""}`}>
                {identity.role}
              </span>
            </b>
          </div>
          <div>
            <span>Permission scope hash</span>
            <b className="mono subtle">{identity.permission_scope_hash}</b>
          </div>
        </div>
      </section>

      <section className="card">
        <h2 className="section-title">Departments you can search</h2>
        {identity.departments.length ? (
          <div className="dept-chips large">
            {identity.departments.map((d) => (
              <span className="chip allow" key={d}>{d}</span>
            ))}
          </div>
        ) : (
          <p className="hint">
            You have no department grants, so every search returns nothing. Ask an
            administrator for access.
          </p>
        )}
        <p className="hint">
          A department you do not hold returns <b>no results at all</b> — results are not
          fetched and then filtered, so the existence of a document is never disclosed.
        </p>
      </section>

      <section className="card">
        <h2 className="section-title">What this means in practice</h2>
        <ul className="plain-list">
          <li>You can only retrieve documents belonging to <span className="mono">{identity.tenant_id}</span>.</li>
          <li>
            Within that tenant you can reach{" "}
            {identity.departments.length ? (
              <b>{identity.departments.join(", ")}</b>
            ) : (
              <b>nothing</b>
            )}
            .
          </li>
          <li>
            {identity.is_admin
              ? "As an administrator you can read operational metrics for your tenant."
              : "Operational metrics are restricted to administrators."}
          </li>
          <li>
            Your permission scope is part of the answer cache key, so a cached answer can
            never be served across a permission boundary.
          </li>
        </ul>
      </section>
    </div>
  );
}
