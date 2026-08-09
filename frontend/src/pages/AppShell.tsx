import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { health, type Health } from "../api/client";

export function AppShell() {
  const { identity, signOut } = useAuth();
  const navigate = useNavigate();
  const [dark, setDark] = useState(() =>
    document.documentElement.classList.contains("dark"),
  );
  const [status, setStatus] = useState<Health | null>(null);
  const [navOpen, setNavOpen] = useState(false);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    localStorage.setItem("ekba-theme", dark ? "dark" : "light");
  }, [dark]);

  useEffect(() => {
    health()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  if (!identity) return null;

  return (
    <div className="app">
      <header className="app-header">
        <button
          className="ghost nav-toggle"
          onClick={() => setNavOpen((o) => !o)}
          aria-label="Toggle navigation"
          aria-expanded={navOpen}
        >
          ☰
        </button>
        <Link className="brand" to="/app/chat">
          <span className="brand-mark" aria-hidden="true">
            ◆
          </span>
          <span className="brand-name">EKBA</span>
        </Link>
        <span className="env-badge mono">dev</span>

        <div className="app-header-right">
          {status && (
            <span
              className={`status status-${status.status === "ready" ? "ok" : "warn"} mono`}
              title={JSON.stringify(status.checks)}
            >
              {status.status}
            </span>
          )}
          <button
            className="ghost"
            onClick={() => setDark((d) => !d)}
            aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
          >
            {dark ? "☀" : "☾"}
          </button>
          <div className="whoami">
            <span className="whoami-email mono">{identity.email}</span>
            <span className={`role-pill ${identity.is_admin ? "admin" : ""}`}>
              {identity.role}
            </span>
          </div>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => {
              signOut();
              navigate("/");
            }}
          >
            Sign out
          </button>
        </div>
      </header>

      <div className="app-body">
        <nav
          className={`sidebar ${navOpen ? "open" : ""}`}
          onClick={() => setNavOpen(false)}
        >
          <p className="nav-group">Ask</p>
          <NavLink to="/app/chat" className="nav-item">
            Chat
          </NavLink>
          <NavLink to="/app/search" className="nav-item">
            Search
          </NavLink>

          <p className="nav-group">Knowledge</p>
          <NavLink to="/app/documents" className="nav-item">
            Documents
          </NavLink>

          <p className="nav-group">Account</p>
          <NavLink to="/app/access" className="nav-item">
            My access
          </NavLink>

          {/* Hidden for non-admins as a courtesy. The API denies them either way. */}
          {identity.is_admin && (
            <>
              <p className="nav-group">Admin</p>
              <NavLink to="/app/admin" className="nav-item">
                Operations
              </NavLink>
            </>
          )}

          <div className="nav-scope">
            <p className="nav-scope-title">Your scope</p>
            <p className="mono">{identity.tenant_id}</p>
            <div className="dept-chips">
              {identity.departments.map((d) => (
                <span className="chip" key={d}>
                  {d}
                </span>
              ))}
              {identity.departments.length === 0 && (
                <span className="chip muted">no departments</span>
              )}
            </div>
          </div>
        </nav>

        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
