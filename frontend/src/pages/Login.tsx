import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

/** The dev seed accounts, offered as one-click fills so RBAC is easy to compare. */
const DEMO = [
  { email: "alice@acme.test", label: "Acme · HR only", role: "user" },
  { email: "bob@acme.test", label: "Acme · Finance only", role: "user" },
  { email: "admin@acme.test", label: "Acme · Admin", role: "admin" },
  { email: "carol@globex.test", label: "Globex · HR only", role: "user" },
];

export function Login() {
  const { identity, signIn, busy, error } = useAuth();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  if (identity) {
    const to = (location.state as { from?: string })?.from ?? "/app/chat";
    return <Navigate to={to} replace />;
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    await signIn(email.trim(), password);
  }

  return (
    <div className="auth-page">
      <div className="auth-card">
        <Link className="brand auth-brand" to="/">
          <span className="brand-mark" aria-hidden="true">◆</span>
          <span className="brand-name">EKBA</span>
        </Link>
        <h1>Sign in</h1>
        <p className="auth-sub">
          Your tenant, role and department access are decided by the server, not by
          this page.
        </p>

        <form onSubmit={submit} className="auth-form">
          <label htmlFor="email">Work email</label>
          <input
            id="email"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            disabled={busy}
          />

          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            disabled={busy}
          />

          {error && (
            <p className="auth-error" role="alert">
              {error}
            </p>
          )}

          <button className="btn btn-primary" type="submit" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <div className="auth-demo">
          <p className="auth-demo-title">Demo accounts (dev)</p>
          <p className="auth-demo-hint">
            Sign in as each to see the same question answered differently — or refused.
          </p>
          <ul>
            {DEMO.map((d) => (
              <li key={d.email}>
                <button
                  type="button"
                  className="demo-pick"
                  onClick={() => setEmail(d.email)}
                  disabled={busy}
                >
                  <span className="mono">{d.email}</span>
                  <span className="demo-role">{d.label}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
