import type { Citation } from "../api/client";

/**
 * A citation that fails to resolve renders as a visible error — never silently
 * dropped and never quietly downgraded to plain text.
 */
export function CitationChip({ citation }: { citation: Citation }) {
  const valid = Boolean(citation.chunk_id && citation.document_name);
  if (!valid) {
    return (
      <span className="chip chip-invalid" role="alert" title="Citation could not be resolved">
        unresolved citation
      </span>
    );
  }
  const where =
    citation.time_offset_ms != null
      ? `${Math.floor(citation.time_offset_ms / 1000)}s`
      : `p.${citation.page_number}`;
  return (
    <span className="chip" title={`${citation.document_name} v${citation.document_version}`}>
      <span className="chip-doc">{citation.document_name}</span>
      <span className="chip-loc mono">{where}</span>
      <span className="chip-id mono">{citation.chunk_id.slice(0, 8)}</span>
    </span>
  );
}
