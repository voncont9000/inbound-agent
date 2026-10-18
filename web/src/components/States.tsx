export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return <p role="status" className="state">{label}</p>;
}

export function ErrorState({ message }: { message: string }) {
  return <p role="alert" className="state state-error">{message}</p>;
}

export function EmptyState({ children }: { children: React.ReactNode }) {
  return <p className="state">{children}</p>;
}

export function Badge({ children, tone = "neutral" }: { children: React.ReactNode; tone?: "neutral" | "success" | "warning" | "danger" }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function FieldError({ id, children }: { id: string; children?: string }) {
  return children ? <span id={id} className="field-error">{children}</span> : null;
}

export function ErrorSummary({ errors }: { errors: Record<string, string> }) {
  const entries = Object.entries(errors);
  if (!entries.length) return null;
  return (
    <section className="error-summary" aria-labelledby="error-summary-title">
      <h2 id="error-summary-title">There are {entries.length} errors to fix</h2>
      <ul>{entries.map(([path, message]) => (
        <li key={path}><a href={`#field-${path}`} onClick={(event) => {
          event.preventDefault();
          document.getElementById(`field-${path}`)?.focus();
        }}>{message}</a></li>
      ))}</ul>
    </section>
  );
}
