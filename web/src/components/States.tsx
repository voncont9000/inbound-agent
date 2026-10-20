import type { ReactNode } from "react";

const glyph = (path: string) => <svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d={path} /></svg>;

export function DeleteIcon() {
  return <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18M8 6V4h8v2m3 0-.8 14H5.8L5 6m4 4v6m6-6v6" /></svg>;
}

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return <div role="status" className="state state-inline"><span className="spinner" aria-hidden="true" />{label}</div>;
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return <div role="alert" className="state state-error">
    <span className="state-icon">{glyph("M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0")}</span>
    <p>{message}</p>
    {onRetry && <button type="button" className="button-sm" onClick={onRetry}>Try again</button>}
  </div>;
}

export function EmptyState({ title, children, action }: { title?: string; children: ReactNode; action?: ReactNode }) {
  return <div className="state">
    <span className="state-icon">{glyph("M22 12h-6l-2 3h-4l-2-3H2M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.7 4H7.3a2 2 0 0 0-1.8 1.1")}</span>
    {title && <h3>{title}</h3>}
    <p>{children}</p>
    {action && <div className="row">{action}</div>}
  </div>;
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "success" | "warning" | "danger" | "info" }) {
  return <span className={`badge badge-${tone}`}>
    {(tone === "success" || tone === "danger") && glyph(tone === "success" ? "m5 12 4 4L19 6" : "M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0")}
    {children}
  </span>;
}

export function PageHeader({ eyebrow, title, lede, actions }: { eyebrow?: string; title: ReactNode; lede?: ReactNode; actions?: ReactNode }) {
  return <header className="page-header">
    <div className="page-header-text">
      {eyebrow && <p className="eyebrow">{eyebrow}</p>}
      <h1>{title}</h1>
      {lede && <p className="lede">{lede}</p>}
    </div>
    {actions && <div className="page-header-actions">{actions}</div>}
  </header>;
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
