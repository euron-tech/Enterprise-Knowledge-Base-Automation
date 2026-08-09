/**
 * Chat view. Citations, refusals, clarifications and agent activity are
 * first-class states — see docs/DESIGN-SYSTEM.md §8.
 */
import { useState, type FormEvent } from "react";
import { askQuestion, type ChatResponse } from "../../api/client";
import { CitationChip } from "../../components/CitationChip";
import { RefusalCard } from "../../components/RefusalCard";
import { ResponseMeta } from "../../components/ResponseMeta";
import { AgentActivity } from "../../components/AgentActivity";

const REFUSAL =
  "I could not find enough evidence in the approved documents to answer this question.";

const SUGGESTIONS = [
  "How many weeks of parental leave?",
  "How does annual leave accrue?",
  "What is the travel expense approval limit?",
  "What is the CFO sign-off threshold?",
];

interface Turn {
  question: string;
  response: ChatResponse;
}

export function Chat() {
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function ask(q: string) {
    if (!q.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const response = await askQuestion(q);
      setTurns((t) => [{ question: q, response }, ...t]);
      setQuestion("");
    } catch (err) {
      // A refusal is not an error; only real failures land here.
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    void ask(question);
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Ask your documents</h1>
        <p className="page-sub">
          Answers come only from documents you are permitted to see, with citations. If
          the evidence is not there, you will be told so.
        </p>
      </div>

      <form onSubmit={submit} className="composer">
        <label htmlFor="q" className="sr-only">
          Ask a question
        </label>
        <input
          id="q"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask about your department's documents…"
          autoComplete="off"
          disabled={busy}
        />
        <button className="btn btn-primary" type="submit" disabled={busy || !question.trim()}>
          {busy ? "Thinking…" : "Ask"}
        </button>
      </form>

      {turns.length === 0 && !busy && (
        <div className="suggestions">
          <span className="hint">Try:</span>
          {SUGGESTIONS.map((s) => (
            <button key={s} type="button" className="suggestion" onClick={() => void ask(s)}>
              {s}
            </button>
          ))}
        </div>
      )}

      {busy && <AgentActivity />}

      {error && (
        <div className="card error" role="alert">
          <strong>Something went wrong.</strong>
          <p>{error}</p>
        </div>
      )}

      {turns.map((turn, i) => {
        const r = turn.response;
        const refused = r.answer === REFUSAL;
        const clarifying = r.terminal_reason === "clarification_requested";
        return (
          <div className="turn" key={`${r.trace_id}-${i}`}>
            <p className="turn-q mono">{turn.question}</p>

            {refused && <RefusalCard message={r.answer} />}

            {clarifying && (
              <div className="card clarify" role="status">
                <strong>One quick question</strong>
                <p>{r.answer}</p>
              </div>
            )}

            {!refused && !clarifying && (
              <article className="card answer" aria-live="polite">
                <p className="answer-text">{r.answer}</p>

                {r.citations.length > 0 && (
                  <section className="citations" aria-label="Citations">
                    <h3>Sources</h3>
                    <ul>
                      {r.citations.map((c) => (
                        <li key={c.chunk_id}>
                          <CitationChip citation={c} />
                        </li>
                      ))}
                    </ul>
                  </section>
                )}

                <ResponseMeta response={r} />
              </article>
            )}

            {(refused || clarifying) && <ResponseMeta response={r} />}
          </div>
        );
      })}
    </div>
  );
}
