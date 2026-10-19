import type { Schemas } from "../../api/client";

type TemplatePickerProps = {
  templates: Schemas["TemplateResponse"][];
  selectedTemplate: string;
  onSelect: (name: string) => void;
  onCreateDemo: (template: Schemas["TemplateResponse"]) => void;
  demoPending: boolean;
};

export function TemplatePicker({
  templates,
  selectedTemplate,
  onSelect,
  onCreateDemo,
  demoPending,
}: TemplatePickerProps) {
  return <fieldset className="template-picker-fieldset">
    <legend>Start from template</legend>
    <p className="help">Industry templates add rules, intake fields and urgency rules. Blank adds none.</p>
    <div className="template-grid">
      {templates.map((template) => <article
        className={`template-card${selectedTemplate === template.name ? " template-card-selected" : ""}`}
        key={template.name}
      >
        <label className="template-choice">
          <input
            type="radio"
            name="setup-template"
            value={template.name}
            checked={selectedTemplate === template.name}
            onChange={() => onSelect(template.name)}
          />
          <span className="template-copy">
            <strong>{template.label}</strong>
            <span>{template.description}</span>
            {template.sets_up.length > 0 && <ul>
              {template.sets_up.map((item) => <li key={item}>{item}</li>)}
            </ul>}
          </span>
        </label>
        {template.sample_business_name && <div className="template-demo">
          <button
            className="button-secondary"
            type="button"
            disabled={demoPending}
            onClick={() => onCreateDemo(template)}
          >
            Create demo assistant
          </button>
          <p className="muted">Uses sample business details. Replace them before going live.</p>
        </div>}
      </article>)}
    </div>
  </fieldset>;
}
