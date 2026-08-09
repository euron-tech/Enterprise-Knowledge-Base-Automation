import { useEffect, useState } from "react";
import { Chat } from "./features/chat/Chat";
import { health, type Health } from "./api/client";

export function App() {
  const [dark, setDark] = useState(
    () => document.documentElement.classList.contains("dark"),
  );
  const [status, setStatus] = useState<Health | null>(null);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    localStorage.setItem("ekba-theme", dark ? "dark" : "light");
  }, [dark]);

  useEffect(() => {
    health().then(setStatus).catch(() => setStatus(null));
  }, []);

  return (
    <div className="shell">
      <header className="header">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">◆</span>
          <span className="brand-name">EKBA</span>
          <span className="env-badge mono">dev</span>
        </div>
        <div className="header-right">
          {status && (
            <span
              className={`status status-${status.status === "ready" ? "ok" : "warn"} mono`}
              title={JSON.stringify(status.checks)}
            >
              {status.status}
            </span>
          )}
          <button
            type="button"
            className="ghost"
            onClick={() => setDark((d) => !d)}
            aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
          >
            {dark ? "☀" : "☾"}
          </button>
        </div>
      </header>

      <main className="main">
        <div className="intro">
          <h1>Ask your department&rsquo;s documents</h1>
          <p>
            Answers come only from approved documents, with citations. When the
            evidence isn&rsquo;t there, you get told so rather than guessed at.
          </p>
        </div>
        <Chat />
      </main>

      <footer className="footer mono">
        <span>EKBA v0.1.0</span>
        <span>dev · ap-south-1</span>
        <a href="/healthz">status</a>
      </footer>
    </div>
  );
}
