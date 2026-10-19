import { useMemo, useState } from "react";
import type { Schemas } from "../../api/client";
import type { AssistantConfig } from "../../lib/config";
import { enumAt } from "../../lib/configSchema";
import { followLabel, humanizeKey, keyFromLabel } from "../../lib/keys";
import { Field, MoreOptions, type SettingsTabProps } from "./fields";
import { invalidProps, hasErrorUnder } from "./errors";

type FieldSpec = Schemas["FieldSpec"];

function plural(count: number, one: string, many = `${one}s`) {
  return `${count} ${count === 1 ? one : many}`;
}

/** Structured details that are rendered into the prompt next to the instructions. */
export function PromptDetails(props: SettingsTabProps) {
  const { config, errors } = props;
  const serviceCount = Object.keys(config.services).length;
  const fieldCount = Object.values(config.services).reduce((total, service) => total + service.fields.length, 0);
  const contactCount = Object.keys(config.contacts).length;
  return <div className="prompt-details stack">
    <p className="help">Added to the prompt with your instructions. Keep facts here rather than in the text above so the assistant and extraction use the same names.</p>
    <div className="prompt-detail">
      <h3>Services and intake fields</h3>
      <p className="muted">{plural(serviceCount, "service")}, {plural(fieldCount, "intake field")}. Field keys are used to extract and confirm caller details.</p>
      <MoreOptions label="Edit services and intake fields" forceOpen={hasErrorUnder(errors, ["services"])}><ServicesEditor {...props} /></MoreOptions>
    </div>
    <div className="prompt-detail">
      <h3>Prices</h3>
      <p className="muted">{config.price_list.length ? plural(config.price_list.length, "published price") : "No published prices."}</p>
      <MoreOptions label="Edit prices" forceOpen={hasErrorUnder(errors, ["price_list"])}><PriceListEditor {...props} /></MoreOptions>
    </div>
    <div className="prompt-detail">
      <h3>Contact details</h3>
      <p className="muted">{contactCount ? `${plural(contactCount, "contact")} the assistant may share.` : "No contact details to share."}</p>
      <MoreOptions label="Edit contact details" forceOpen={hasErrorUnder(errors, ["contacts"])}><ContactsEditor {...props} /></MoreOptions>
    </div>
  </div>;
}

function PriceListEditor({ config, update }: SettingsTabProps) {
  return <>
      {config.price_list.map((item, index) => <div className="inline-editor" key={`price-${index}`}>
        <div className="form-grid">{(["service", "price", "notes"] as const).map((key) => <Field key={key} path={`price_list.${index}.${key}`} label={key.replaceAll("_", " ")}>{(fieldId) => key === "notes" ? <textarea id={fieldId} value={item[key]} onChange={(event) => update((draft) => { draft.price_list[index][key] = event.target.value; })} /> : <input id={fieldId} value={item[key]} onChange={(event) => update((draft) => { draft.price_list[index][key] = event.target.value; })} />}</Field>)}</div>
        <button type="button" onClick={() => update((draft) => { draft.price_list.splice(index, 1); })}>Remove price</button>
      </div>)}
      <button type="button" onClick={() => update((draft) => { draft.price_list.push({ service: "", price: "", notes: "" }); })}>Add price</button>
  </>;
}

function ContactsEditor({ config, saved, update }: SettingsTabProps) {
  const [contactLabels, setContactLabels] = useState<Record<number, string>>({});
  return <>
      {Object.entries(config.contacts).map(([key, value], index) => {
        const isSaved = key in saved.contacts;
        const label = contactLabels[index] ?? humanizeKey(key);
        return <div className="form-grid" key={`contact-${index}`}>
          {isSaved
            ? <Field path={`contacts.${key}.value`} label={humanizeKey(key)}>{(fieldId) => <input id={fieldId} value={value} onChange={(event) => update((draft) => { draft.contacts[key] = event.target.value; })} />}</Field>
            : <>
              <Field path={`contacts.${key}.label`} label="Contact name">{(fieldId) => <input id={fieldId} value={label} onChange={(event) => {
                const nextLabel = event.target.value;
                setContactLabels((current) => ({ ...current, [index]: nextLabel }));
                update((draft) => {
                  const nextKey = followLabel({ key, label: nextLabel, taken: Object.keys(draft.contacts), saved: Object.keys(saved.contacts), fallback: "contact" });
                  draft.contacts = Object.fromEntries(Object.entries(draft.contacts).map(([itemKey, itemValue]) => [itemKey === key ? nextKey : itemKey, itemValue]));
                });
              }} />}</Field>
              <Field path={`contacts.${key}.value`} label="Contact value">{(fieldId) => <input id={fieldId} value={value} onChange={(event) => update((draft) => { draft.contacts[key] = event.target.value; })} />}</Field>
            </>}
          <button type="button" onClick={() => { setContactLabels({}); update((draft) => { delete draft.contacts[key]; }); }}>Remove contact</button>
        </div>;
      })}
      <button type="button" onClick={() => update((draft) => { draft.contacts[keyFromLabel("", Object.keys(draft.contacts), "contact")] = ""; })}>Add contact</button>
  </>;
}

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
