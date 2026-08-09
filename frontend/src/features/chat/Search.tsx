import { useState, type FormEvent } from "react";
import { search, type SearchHit } from "../../api/client";

/**
 * Retrieval without generation. Useful for seeing the boundary directly: the same
 * query returns different chunks, or nothing at all, depending on who is asking.
 */
export function Search() {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!query.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      setHits((await search(query, 10)).results);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed");
      setHits(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Search</h1>
        <p className="page-sub">
          Raw retrieval, no model. Shows exactly which chunks your scope can reach.
        </p>
      </div>

      <form className="composer" onSubmit={submit}>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="e.g. parental leave, expense approval, NDA term"
          aria-label="Search query"
          disabled={busy}
        />
        <button className="btn btn-primary" type="submit" disabled={busy || !query.trim()}>
          {busy ? "Searching…" : "Search"}
        </button>
      </form>

      {error && <div className="card error" role="alert">{error}</div>}

      {hits !== null && hits.length === 0 && (
        <div className="card muted">
          <strong>No results.</strong>
          <p className="hint">
            Either nothing matches, or the matching document is outside your departments.
            The two are deliberately indistinguishable from here.
          </p>
        </div>
      )}

      {hits?.map((h) => (
        <article className="card hit" key={h.chunk_id}>
          <div className="hit-head">
            <span className="hit-doc">{h.document_name}</span>
            <span className="mono subtle">p.{h.page_number}</span>
            <span className="score mono">{h.score.toFixed(3)}</span>
          </div>
          <p className="hit-text">{h.text}</p>
          <span className="mono subtle tiny">{h.chunk_id.slice(0, 16)}…</span>
        </article>
      ))}
    </div>
  );
}
