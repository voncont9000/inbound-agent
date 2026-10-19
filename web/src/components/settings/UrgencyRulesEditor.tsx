import { useState } from "react";
import type { AssistantConfig } from "../../lib/config";
import { Field, type SettingsTabProps } from "./fields";
import { invalidProps } from "./errors";

type DateDeadlineRule = Extract<AssistantConfig["urgency_rules"][number], { type: "date_deadline" }>;

const DATE_DEADLINE_FIELD_LABELS = {
  add_months: "Months after the date",
  minus_days: "Days before that",
  urgent_within_days: "Urgent when this many days or fewer remain",
} as const;

function signedAmount(amount: number, unit: string) {
  const value = Math.abs(amount);
  const label = value === 1 ? unit : `${unit}s`;
  return `${amount > 0 ? "+" : "−"} ${value} ${label}`;
}

function deadlinePreview(fieldLabel: string, rule: DateDeadlineRule) {
  const adjustments = [
    ...(rule.add_months === 0 ? [] : [signedAmount(rule.add_months, "month")]),
    ...(rule.minus_days === 0 ? [] : [signedAmount(-rule.minus_days, "day")]),
  ];
  const deadline = `Deadline: ${fieldLabel}${adjustments.length ? ` ${adjustments.join(" ")}` : ""}.`;
  const urgency = rule.urgent_within_days === 0
    ? ""
    : ` Urgent when ${rule.urgent_within_days} ${rule.urgent_within_days === 1 ? "day" : "days"} or fewer remain.`;
  return `${deadline}${urgency}`;
}

function dateFieldLabel(config: AssistantConfig, rule: DateDeadlineRule) {
  return config.services[rule.service]?.fields.find(({ name, type }) => name === rule.field && type === "date")?.label.trim();
}

export function UrgencyRulesEditor({ config, update, errors }: Pick<SettingsTabProps, "config" | "update" | "errors">) {
  const [phraseText, setPhraseText] = useState<Record<string, string>>({});
  const [moreRulesOpen, setMoreRulesOpen] = useState(() =>
    config.urgency_rules.some((rule) => rule.type === "keyword" || rule.type === "date_deadline"),
  );
  const serviceKeys = Object.keys(config.services);
  const sentenceRules = config.urgency_rules.flatMap((rule, index) =>
    rule.type === "sentence" ? [{ rule, index }] : [],
  );

  return <section className="panel stack" aria-labelledby="urgency-heading">
    <h2 id="urgency-heading">Urgency</h2>
    <div className="stack">
      <h3>Urgent if…</h3>
      <p className="help">
        Plain sentences checked after each conversation; a match creates an urgent task with the reason.
      </p>
      {sentenceRules.map(({ rule, index }, position) => <div className="inline-editor" key={`sentence-rule-${index}`}>
        <Field
          path={`urgency_rules.${index}.text`}
          label={`Urgent if… ${position + 1}`}
          error={errors[`urgency_rules.${index}.text`]}
        >
          {(fieldId, describedBy) => <input
            id={fieldId}
            placeholder="Urgent if the customer needs help today"
            value={rule.text}
            aria-describedby={describedBy}
            onChange={(event) => update((draft) => {
              const current = draft.urgency_rules[index];
              if (current?.type === "sentence") current.text = event.target.value;
            })}
          />}
        </Field>
        <button type="button" onClick={() => update((draft) => { draft.urgency_rules.splice(index, 1); })}>
          Remove urgent rule
        </button>
      </div>)}
      <button type="button" onClick={() => update((draft) => {
        draft.urgency_rules.push({ type: "sentence", text: "" });
      })}>Add urgent rule</button>
    </div>
    <details
      className="stack"
      open={moreRulesOpen}
      onToggle={(event) => setMoreRulesOpen(event.currentTarget.open)}
    >
      <summary>More rule types: keywords and date deadlines</summary>
      <p className="help">Use keyword phrases or a collected date when those rules suit the business.</p>
      {config.urgency_rules.flatMap((rule, index) => {
        if (rule.type === "sentence") return [];
        const fieldLabel = rule.type === "date_deadline" ? dateFieldLabel(config, rule) : undefined;
        return [<section className="inline-editor" key={`urgency-${index}`}>
          <Field path={`urgency_rules.${index}.type`} label="Rule type">{(fieldId) => <select id={fieldId} value={rule.type} onChange={(event) => {
            setPhraseText((current) => { const next = { ...current }; delete next[`urgency_rules.${index}.phrases`]; return next; });
            update((draft) => {
              draft.urgency_rules[index] = event.target.value === "keyword" ? { type: "keyword", phrases: [], reason: "" } : { type: "date_deadline", service: serviceKeys[0] ?? "", field: "", add_months: 0, minus_days: 0, urgent_within_days: 0, reason: "" };
            });
          }}><option value="keyword">Keywords</option><option value="date_deadline">Date deadline</option></select>}</Field>
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
            {(["add_months", "minus_days", "urgent_within_days"] as const).map((key) => <Field key={key} path={`urgency_rules.${index}.${key}`} label={DATE_DEADLINE_FIELD_LABELS[key]} error={errors[`urgency_rules.${index}.${key}`]}>{(fieldId, describedBy) => <input id={fieldId} type="number" min="0" step="1" value={rule[key]} {...invalidProps(errors, `urgency_rules.${index}.${key}`)} aria-describedby={describedBy} onChange={(event) => update((draft) => { const current = draft.urgency_rules[index]; if (current?.type === "date_deadline") current[key] = Number(event.target.value); })} />}</Field>)}
            {fieldLabel && <p className="help">{deadlinePreview(fieldLabel, rule)}</p>}
            <Field path={`urgency_rules.${index}.reason`} label="Reason" error={errors[`urgency_rules.${index}.reason`]}>{(fieldId, describedBy) => <input id={fieldId} value={rule.reason} {...invalidProps(errors, `urgency_rules.${index}.reason`)} aria-describedby={describedBy} onChange={(event) => update((draft) => { const current = draft.urgency_rules[index]; if (current?.type === "date_deadline") current.reason = event.target.value; })} />}</Field>
          </>}
          <button type="button" onClick={() => { setPhraseText({}); update((draft) => { draft.urgency_rules.splice(index, 1); }); }}>Remove urgency rule</button>
        </section>];
      })}
      <button type="button" onClick={() => update((draft) => { draft.urgency_rules.push({ type: "keyword", phrases: [], reason: "" }); })}>Add urgency rule</button>
    </details>
  </section>;
}
