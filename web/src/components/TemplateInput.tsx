import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { slugVariableName, type TemplateVariableOption } from "../lib/templateVariables";

export function TemplateInput({
  id,
  label,
  ariaLabel,
  value,
  onChange,
  variables,
  multiline = false,
  firstField = false,
}: {
  id: string;
  label: string;
  ariaLabel?: string;
  value: string;
  onChange: (value: string) => void;
  variables: TemplateVariableOption[];
  multiline?: boolean;
  firstField?: boolean;
}) {
  const menuId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const menuContainerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const customNameRef = useRef<HTMLInputElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [customVariableOpen, setCustomVariableOpen] = useState(false);
  const [customName, setCustomName] = useState("");
  const customSlug = slugVariableName(customName);

  useEffect(() => {
    if (!menuOpen) return;
    const dismissOnOutsidePointerDown = (event: PointerEvent) => {
      if (menuContainerRef.current?.contains(event.target as Node)) return;
      setMenuOpen(false);
      setCustomVariableOpen(false);
      setCustomName("");
    };
    const dismissOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      setMenuOpen(false);
      setCustomVariableOpen(false);
      setCustomName("");
      window.requestAnimationFrame(() => triggerRef.current?.focus());
    };
    document.addEventListener("pointerdown", dismissOnOutsidePointerDown);
    document.addEventListener("keydown", dismissOnEscape);
    return () => {
      document.removeEventListener("pointerdown", dismissOnOutsidePointerDown);
      document.removeEventListener("keydown", dismissOnEscape);
    };
  }, [menuOpen]);

  const insertVariable = (name: string) => {
    const field = multiline ? textareaRef.current : inputRef.current;
    const start = field?.selectionStart ?? value.length;
    const end = field?.selectionEnd ?? value.length;
    const insertion = `{{${name}}}`;
    onChange(`${value.slice(0, start)}${insertion}${value.slice(end)}`);
    setMenuOpen(false);
    setCustomVariableOpen(false);
    setCustomName("");
    field?.focus();
    window.requestAnimationFrame(() => {
      const currentField = multiline ? textareaRef.current : inputRef.current;
      if (!currentField) return;
      const cursor = start + insertion.length;
      currentField.setSelectionRange(cursor, cursor);
    });
  };

  const toggleMenu = () => {
    setMenuOpen((open) => !open);
    setCustomVariableOpen(false);
    setCustomName("");
  };

  const startCustomVariable = () => {
    setCustomVariableOpen(true);
    window.requestAnimationFrame(() => customNameRef.current?.focus());
  };

  const submitCustomVariable = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (customSlug) insertVariable(customSlug);
  };

  return <div className="field template-input">
    <div ref={menuContainerRef} className={`template-input-label-row${menuOpen ? " template-input-label-row-open" : ""}`}>
      <label htmlFor={id}>{label}</label>
      <button ref={triggerRef} type="button" className="button-secondary template-input-trigger" aria-expanded={menuOpen} aria-controls={menuId} onClick={toggleMenu}>Insert variable</button>
      {menuOpen && <div id={menuId} className="template-variable-menu" aria-label={`${label} variables`}>
        <div className="template-variable-options">
          {variables.map((variable) => {
            const sameDescription = variable.description.trim().replace(/\.$/, "").toLowerCase() === variable.label.trim().toLowerCase();
            return <button key={variable.name} type="button" className="template-variable-option" onClick={() => insertVariable(variable.name)}>
              <span><strong>{variable.label}</strong>{!sameDescription && <small>{variable.description}</small>}</span>
            </button>;
          })}
          {variables.length === 0 && <p className="help">No variables are available yet.</p>}
          <button type="button" className="template-variable-option template-variable-custom" onClick={startCustomVariable}>
            <span><strong>Custom variable…</strong><small>Insert a value provided by your workflow.</small></span>
          </button>
        </div>
        {customVariableOpen && <form className="template-custom-variable" onSubmit={submitCustomVariable}>
          <label htmlFor={`${menuId}-custom-name`}>Variable name</label>
          <input ref={customNameRef} id={`${menuId}-custom-name`} aria-label="Custom variable name" value={customName} onChange={(event) => setCustomName(event.target.value)} onKeyDown={(event) => {
            if (event.key === "Enter") event.stopPropagation();
          }} />
          {customName.trim() && !customSlug && <span className="field-error">Use letters, numbers, underscores or periods.</span>}
          <button type="submit" className="button-secondary" disabled={!customSlug}>Insert variable</button>
        </form>}
      </div>}
    </div>
    {multiline
      ? <textarea ref={textareaRef} id={id} aria-label={ariaLabel} data-action-first-field={firstField || undefined} value={value} onChange={(event) => onChange(event.target.value)} />
      : <input ref={inputRef} id={id} aria-label={ariaLabel} data-action-first-field={firstField || undefined} value={value} onChange={(event) => onChange(event.target.value)} />}
  </div>;
}
