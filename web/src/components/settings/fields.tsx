import { useId, useState, type ReactNode } from "react";
import type { AssistantConfig } from "../../lib/config";
import { FieldError } from "../States";

export type UpdateConfig = (change: (draft: AssistantConfig) => void) => void;

export type SettingsTabProps = {
  assistantId: string;
  config: AssistantConfig;
  /** Last saved configuration; keys present here are never regenerated. */
  saved: AssistantConfig;
  update: UpdateConfig;
  errors: Record<string, string>;
  clearFieldError: (path: string) => void;
  schema: unknown;
};

export function Field({ path, label, error, help, children }: { path: string; label: string; error?: string; help?: string; children: (id: string, describedBy?: string) => ReactNode }) {
  const id = `field-${path}`;
  const errorId = `${id}-error`;
  const describedBy = [error ? errorId : "", help ? `${id}-help` : ""].filter(Boolean).join(" ") || undefined;
  return <div className="field">
    <label htmlFor={id}>{label}</label>
    {children(id, describedBy)}
    {help && <span id={`${id}-help`} className="help">{help}</span>}
    <FieldError id={errorId}>{error}</FieldError>
  </div>;
}

/** Progressive disclosure for optional fields; opens itself when a field inside has an error. */
export function MoreOptions({ label = "More options", forceOpen = false, children }: { label?: string; forceOpen?: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const expanded = open || forceOpen;
  return <div className="more-options">
    <button type="button" className="button-link" aria-expanded={expanded} aria-controls={id} onClick={() => setOpen(!expanded)}>
      {expanded ? `Hide ${label.toLowerCase()}` : label}
    </button>
    {expanded && <div id={id} className="stack">{children}</div>}
  </div>;
}

export function AbilityCard({ id, title, description, enabled, onToggle, children, status }: {
  id: string;
  title: string;
  description: string;
  enabled: boolean;
  onToggle: (enabled: boolean) => void;
  children?: ReactNode;
  status?: ReactNode;
}) {
  const headingId = `ability-${id}-heading`;
  return <section className={`panel stack ability-card${enabled ? " ability-card-on" : ""}`} aria-labelledby={headingId}>
    <div className="ability-card-header">
      <div>
        <h2 id={headingId}>{title}</h2>
        <p className="muted">{description}</p>
      </div>
      <label className="switch">
        <input type="checkbox" role="switch" aria-checked={enabled} checked={enabled} aria-label={title} onChange={(event) => onToggle(event.target.checked)} />
        <span aria-hidden="true">{enabled ? "On" : "Off"}</span>
      </label>
    </div>
    {status}
    {enabled && children}
  </section>;
}
