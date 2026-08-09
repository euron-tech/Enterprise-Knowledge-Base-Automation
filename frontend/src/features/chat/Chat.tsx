/**
 * Chat view. Citations, refusals, clarifications and agent activity are
 * first-class states — see docs/DESIGN-SYSTEM.md §8.
 */
import { useState } from "react";
import { askQuestion, type ChatResponse } from "../../api/client";
import { CitationChip } from "../../components/CitationChip";
import { RefusalCard } from "../../components/RefusalCard";
import { ResponseMeta } from "../../components/ResponseMeta";
import { AgentActivity } from "../../components/AgentActivity";

const REFUSAL =
  "I could not find enough evidence in the approved documents to answer this question.";

export function Chat() {
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [response, setResponse] = useState<ChatResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!question.trim() || busy) return;
    setBusy(true);
    setError(null);
    setResponse(null);
    try {
      setResponse(await askQuestion(question));
    } catch (err) {
      // A refusal is not an error. Only real failures land here.
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }

  const refused = response?.answer === REFUSAL;
  const clarifying = response?.terminal_reason === "clarification_requested";

  return (
    <div className="chat">
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
        <button type="submit" disabled={busy || !question.trim()}>
          {busy ? "Searching…" : "Ask"}
        </button>
      </form>

      {busy && <AgentActivity />}

      {error && (
        <div className="card error" role="alert">
          <strong>Something went wrong.</strong>
          <p>{error}</p>
        </div>
      )}

      {response && refused && <RefusalCard message={response.answer} />}

      {response && clarifying && (
        <div className="card clarify" role="status">
          <strong>One quick question</strong>
          <p>{response.answer}</p>
        </div>
      )}

      {response && !refused && !clarifying && (
        <article className="card answer" aria-live="polite">
          <p className="answer-text">{response.answer}</p>

          {response.citations.length > 0 && (
            <section className="citations" aria-label="Citations">
              <h3>Sources</h3>
              <ul>
                {response.citations.map((c) => (
                  <li key={c.chunk_id}>
                    <CitationChip citation={c} />
                  </li>
                ))}
              </ul>
            </section>
          )}

          <ResponseMeta response={response} />
        </article>
      )}
    </div>
  );
}
