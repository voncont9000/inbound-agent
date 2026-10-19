import { useState } from "react";
import { Field, type SettingsTabProps } from "./fields";
import { invalidProps } from "./errors";

export function UrgencyRulesEditor({ config, update, errors }: Pick<SettingsTabProps, "config" | "update" | "errors">) {
  const [phraseText, setPhraseText] = useState<Record<string, string>>({});
  const serviceKeys = Object.keys(config.services);
  return <>
    <section className="panel stack" aria-labelledby="urgency-heading"><h2 id="urgency-heading">Keyword and date urgency rules</h2>
      <p className="help">Mark a conversation urgent when the caller uses certain phrases, or when a collected date is close.</p>
      {config.urgency_rules.flatMap((rule, index) => rule.type === "sentence" ? [] : [
      <section className="inline-editor" key={`urgency-${index}`}>
        <Field path={`urgency_rules.${index}.type`} label="Rule type">{(fieldId) => <select id={fieldId} value={rule.type} onChange={(event) => {
          setPhraseText((current) => { const next = { ...current }; delete next[`urgency_rules.${index}.phrases`]; return next; });
          update((draft) => {
            draft.urgency_rules[index] = event.target.value === "keyword" ? { type: "keyword", phrases: [], reason: "" } : { type: "date_deadline", service: serviceKeys[0] ?? "", field: "", add_months: 0, minus_days: 0, urgent_within_days: 0, reason: "" };
          });
        }}><option value="keyword">Keyword</option><option value="date_deadline">Date deadline</option></select>}</Field>
        {rule.type === "keyword" ? <>
          <Field path={`urgency_rules.${index}.phrases`} label="Phrases (comma or newline separated)" error={errors[`urgency_rules.${index}.phrases`]}>{(fieldId, describedBy) => {
            const path = `urgency_rules.${index}.phrases`;
            return <textarea id={fieldId} value={phraseText[path] ?? rule.phrases.join(", ")} {...invalidProps(errors, path)} aria-describedby={describedBy} onChange={(event) => {
              const value = event.target.value;
              setPhraseText((current) => ({ ...current, [path]: value }));
              update((draft) => {
                const current = draft.urgency_rules[index];
                if (current?.type === "keyword") current.phrases = value.split(/,|\n/).map((item) => item.trim());
              });
            }} />;
          }}</Field>
          <Field path={`urgency_rules.${index}.reason`} label="Reason" error={errors[`urgency_rules.${index}.reason`]}>{(fieldId, describedBy) => <input id={fieldId} value={rule.reason} {...invalidProps(errors, `urgency_rules.${index}.reason`)} aria-describedby={describedBy} onChange={(event) => update((draft) => { const current = draft.urgency_rules[index]; if (current?.type === "keyword") current.reason = event.target.value; })} />}</Field>
        </> : <>
          <div className="form-grid">
            <Field path={`urgency_rules.${index}.service`} label="Service" error={errors[`urgency_rules.${index}.service`]}>{(fieldId, describedBy) => <select id={fieldId} value={rule.service} {...invalidProps(errors, `urgency_rules.${index}.service`)} aria-describedby={describedBy} onChange={(event) => update((draft) => { const current = draft.urgency_rules[index]; if (current?.type === "date_deadline") { current.service = event.target.value; current.field = ""; } })}>{serviceKeys.map((key) => <option key={key} value={key}>{config.services[key].label || key}</option>)}</select>}</Field>
            <Field path={`urgency_rules.${index}.field`} label="Date field" error={errors[`urgency_rules.${index}.field`]}>{(fieldId, describedBy) => <select id={fieldId} value={rule.field} {...invalidProps(errors, `urgency_rules.${index}.field`)} aria-describedby={describedBy} onChange={(event) => update((draft) => { const current = draft.urgency_rules[index]; if (current?.type === "date_deadline") current.field = event.target.value; })}><option value="">Choose a date field</option>{config.services[rule.service]?.fields.filter(({ type }) => type === "date").map(({ name, label }) => <option key={name} value={name}>{label}</option>)}</select>}</Field>
          </div>
          {(["add_months", "minus_days", "urgent_within_days"] as const).map((key) => <Field key={key} path={`urgency_rules.${index}.${key}`} label={key.replaceAll("_", " ")} error={errors[`urgency_rules.${index}.${key}`]}>{(fieldId, describedBy) => <input id={fieldId} type="number" min="0" step="1" value={rule[key]} {...invalidProps(errors, `urgency_rules.${index}.${key}`)} aria-describedby={describedBy} onChange={(event) => update((draft) => { const current = draft.urgency_rules[index]; if (current?.type === "date_deadline") current[key] = Number(event.target.value); })} />}</Field>)}
          <Field path={`urgency_rules.${index}.reason`} label="Reason" error={errors[`urgency_rules.${index}.reason`]}>{(fieldId, describedBy) => <input id={fieldId} value={rule.reason} {...invalidProps(errors, `urgency_rules.${index}.reason`)} aria-describedby={describedBy} onChange={(event) => update((draft) => { const current = draft.urgency_rules[index]; if (current?.type === "date_deadline") current.reason = event.target.value; })} />}</Field>
        </>}
        <button type="button" onClick={() => { setPhraseText({}); update((draft) => { draft.urgency_rules.splice(index, 1); }); }}>Remove urgency rule</button>
      </section>])}
      <button type="button" onClick={() => update((draft) => { draft.urgency_rules.push({ type: "keyword", phrases: [], reason: "" }); })}>Add urgency rule</button>
    </section>
  </>;
}
