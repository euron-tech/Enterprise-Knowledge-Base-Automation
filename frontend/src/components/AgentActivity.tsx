/**
 * Coarse, whitelisted progress only. The plan, tool arguments and raw tool output
 * are internal telemetry and must never be rendered to the user.
 */
const STEPS = ["Checking your access", "Searching documents", "Reviewing evidence", "Composing answer"];

export function AgentActivity() {
  return (
    <div className="card activity" role="status" aria-live="polite">
      <ul>
        {STEPS.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ul>
    </div>
  );
}
