import { useMemo, useState } from "react";
import type { Schemas } from "../../api/client";
import type { AssistantConfig } from "../../lib/config";
import { enumAt } from "../../lib/configSchema";
import { followLabel, keyFromLabel } from "../../lib/keys";
import { Field, MoreOptions, type SettingsTabProps } from "./fields";
import { hasErrorUnder, invalidProps } from "./errors";

type FieldSpec = Schemas["FieldSpec"];

function renameServiceKey(draft: AssistantConfig, from: string, to: string) {
  if (from === to) return;
  draft.services = Object.fromEntries(Object.entries(draft.services).map(([key, value]) => [key === from ? to : key, value]));
  draft.urgency_rules.forEach((rule) => { if (rule.type === "date_deadline" && rule.service === from) rule.service = to; });
}

export function ServicesEditor({ config, saved, update, errors, schema }: SettingsTabProps) {
  const [choiceText, setChoiceText] = useState<Record<string, string>>({});
  const typeOptions = useMemo(() => enumAt(schema, ["$defs", "FieldSpec", "properties", "type", "enum"], ["string", "email", "phone", "date", "boolean", "integer", "number", "enum", "list"]), [schema]);
  const serviceKeys = Object.keys(config.services);
  return <>
    {serviceKeys.map((serviceKey, serviceIndex) => {
      const service = config.services[serviceKey];
      const savedFieldNames = (saved.services[serviceKey]?.fields ?? []).map(({ name }) => name);
      return <section className="inline-editor stack intake-service" key={`service-${serviceIndex}`} aria-label={service.label || "New service"}>
        <Field path={`services.${serviceKey}.label`} label="Service label" error={errors[`services.${serviceKey}.label`]}>{(fieldId, describedBy) => <input id={fieldId} value={service.label} aria-describedby={describedBy} onChange={(event) => update((draft) => {
          const label = event.target.value;
          draft.services[serviceKey].label = label;
          const nextKey = followLabel({ key: serviceKey, label, taken: Object.keys(draft.services), saved: Object.keys(saved.services), fallback: "service" });
          renameServiceKey(draft, serviceKey, nextKey);
        })} />}</Field>
        {service.fields.map((field, fieldIndex) => {
          const base = `services.${serviceKey}.fields.${fieldIndex}`;
          return <div className="intake-field-chip stack" key={`field-${fieldIndex}`}>
            <div className="form-grid">
              <Field path={`${base}.label`} label="Field label" error={errors[`${base}.label`] ?? errors[`${base}.name`]}>{(fieldId, describedBy) => <input id={fieldId} value={field.label} {...invalidProps(errors, `${base}.label`)} aria-describedby={describedBy} onChange={(event) => update((draft) => {
                const item = draft.services[serviceKey].fields[fieldIndex];
                const previousName = item.name;
                item.label = event.target.value;
                item.name = followLabel({ key: item.name, label: item.label, taken: draft.services[serviceKey].fields.map(({ name }) => name), saved: savedFieldNames, fallback: "field" });
                draft.urgency_rules.forEach((rule) => { if (rule.type === "date_deadline" && rule.service === serviceKey && rule.field === previousName) rule.field = item.name; });
              })} />}</Field>
              <Field path={`${base}.type`} label="Field type" error={errors[`${base}.type`]}>{(fieldId, describedBy) => <select id={fieldId} value={field.type} aria-describedby={describedBy} onChange={(event) => {
                setChoiceText((current) => { const next = { ...current }; delete next[`${base}.choices`]; return next; });
                update((draft) => {
                  const item = draft.services[serviceKey].fields[fieldIndex];
                  item.type = event.target.value as FieldSpec["type"];
                  if (item.type !== "enum") delete item.choices;
                  else item.choices = [];
                });
              }}>{typeOptions.map((type) => <option key={type} value={type}>{type}</option>)}</select>}</Field>
              {field.type === "enum" && <Field path={`${base}.choices`} label="Choices" error={errors[`${base}.choices`]}>{(fieldId, describedBy) => <input id={fieldId} value={choiceText[`${base}.choices`] ?? field.choices?.join(", ") ?? ""} {...invalidProps(errors, `${base}.choices`)} aria-describedby={describedBy} onChange={(event) => {
                const value = event.target.value;
                setChoiceText((current) => ({ ...current, [`${base}.choices`]: value }));
                update((draft) => { draft.services[serviceKey].fields[fieldIndex].choices = value.split(",").map((choice) => choice.trim()); });
              }} />}</Field>}
            </div>
            <div className="button-row">
              <label className="check-row"><input type="checkbox" checked={field.required} onChange={(event) => update((draft) => { draft.services[serviceKey].fields[fieldIndex].required = event.target.checked; })} />Required</label>
              <button type="button" onClick={() => update((draft) => { draft.services[serviceKey].fields.splice(fieldIndex, 1); })}>Remove field</button>
            </div>
            <MoreOptions forceOpen={hasErrorUnder(errors, [`${base}.description`])}>
              <label className="check-row"><input type="checkbox" checked={field.critical} onChange={(event) => update((draft) => { draft.services[serviceKey].fields[fieldIndex].critical = event.target.checked; })} />Critical (read back for confirmation)</label>
              <Field path={`${base}.description`} label="Description">{(fieldId) => <input id={fieldId} value={field.description} onChange={(event) => update((draft) => { draft.services[serviceKey].fields[fieldIndex].description = event.target.value; })} />}</Field>
            </MoreOptions>
          </div>;
        })}
        <div className="button-row">
          <button type="button" onClick={() => update((draft) => {
            const fields = draft.services[serviceKey].fields;
            fields.push({ name: keyFromLabel("", fields.map(({ name }) => name), "field"), label: "", type: "string", required: false, critical: false, description: "" });
          })}>Add field</button>
          <button type="button" onClick={() => update((draft) => {
            delete draft.services[serviceKey];
            draft.urgency_rules = draft.urgency_rules.filter((rule) => rule.type !== "date_deadline" || rule.service !== serviceKey);
          })}>Remove service</button>
        </div>
      </section>;
    })}
    <button type="button" onClick={() => update((draft) => { draft.services[keyFromLabel("", Object.keys(draft.services), "service")] = { label: "", fields: [] }; })}>Add service</button>
  </>;
}
