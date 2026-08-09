/**
 * A refusal is a correct product outcome, not a failure. It is rendered as a
 * first-class state — never as an error toast, never dressed up, never retried.
 */
export function RefusalCard({ message }: { message: string }) {
  return (
    <div className="card refusal" role="status">
      <div className="refusal-head">
        <span aria-hidden="true">◇</span>
        <strong>No supporting evidence found</strong>
      </div>
      <p>{message}</p>
      <p className="refusal-hint">
        Try rephrasing, or request access if the document belongs to another department.
      </p>
    </div>
  );
}
