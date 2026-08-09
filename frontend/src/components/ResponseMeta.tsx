import type { ChatResponse } from "../api/client";

/** Honest metadata. Identifiers are monospaced so they can be compared. */
export function ResponseMeta({ response }: { response: ChatResponse }) {
  const band =
    response.confidence >= 0.7 ? "high" : response.confidence >= 0.4 ? "medium" : "low";
  return (
    <footer className="meta mono">
      <span className={`confidence confidence-${band}`}>
        confidence {response.confidence.toFixed(2)} ({band})
      </span>
      <span>{response.model_used}</span>
      <span>{response.latency_ms} ms</span>
      <span>
        {response.input_tokens}in / {response.output_tokens}out
      </span>
      <span>${response.estimated_cost.toFixed(6)}</span>
      <span>{response.cache_hit ? "cached" : "fresh"}</span>
      <button
        type="button"
        className="trace"
        title="Copy correlation id"
        onClick={() => navigator.clipboard?.writeText(response.trace_id)}
      >
        {response.trace_id.slice(0, 12)}
      </button>
    </footer>
  );
}
