import { KnowledgeSources } from "../KnowledgeSources";
import { Field, type SettingsTabProps } from "./fields";
import { invalidProps } from "./errors";

const days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export function KnowledgeTab({ assistantId, config, update, errors }: SettingsTabProps) {
  const faqs = config.knowledge?.faqs ?? [];
  const setFaqs = (change: (items: typeof faqs) => typeof faqs) => update((draft) => {
    draft.knowledge = { ...draft.knowledge, faqs: change(draft.knowledge?.faqs ?? []), max_digest_chars: draft.knowledge?.max_digest_chars ?? 24000 };
  });
  return <>
    <section className="panel stack" aria-labelledby="office-hours-heading"><h2 id="office-hours-heading">Office hours</h2>
      <fieldset><legend>Open days</legend><div className="button-row">{days.map((day, index) => <label key={day} className="check-row"><input id={index === 0 ? "field-office_hours.days" : undefined} type="checkbox" checked={config.office_hours.days.includes(day)} aria-invalid={Boolean(errors["office_hours.days"])} aria-describedby={errors["office_hours.days"] ? "field-office_hours.days-error" : undefined} onChange={(event) => update((draft) => { draft.office_hours.days = event.target.checked ? [...draft.office_hours.days, day] : draft.office_hours.days.filter((item) => item !== day); })} />{day.slice(0, 3)}</label>)}</div>{errors["office_hours.days"] && <p id="field-office_hours.days-error" className="field-error">{errors["office_hours.days"]}</p>}</fieldset>
      <div className="form-grid">
        {(["opens", "closes"] as const).map((key) => <Field key={key} path={`office_hours.${key}`} label={key === "opens" ? "Opens" : "Closes"} error={errors[`office_hours.${key}`]}>{(fieldId, describedBy) => <input id={fieldId} type="time" value={config.office_hours[key]} {...invalidProps(errors, `office_hours.${key}`)} aria-describedby={describedBy} onChange={(event) => update((draft) => { draft.office_hours[key] = event.target.value; })} />}</Field>)}
        <Field path="office_hours.timezone" label="Timezone">{(fieldId) => <input id={fieldId} value={config.office_hours.timezone} onChange={(event) => update((draft) => { draft.office_hours.timezone = event.target.value; })} />}</Field>
      </div>
    </section>
    <section className="panel stack" aria-labelledby="faq-heading"><h2 id="faq-heading">Frequently asked questions</h2>
      {faqs.map((faq, index) => <div className="inline-editor" key={`faq-${index}`}>
        <Field path={`knowledge.faqs.${index}.question`} label="Question">{(fieldId) => <input id={fieldId} value={faq.question} onChange={(event) => setFaqs((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, question: event.target.value } : item))} />}</Field>
        <Field path={`knowledge.faqs.${index}.answer`} label="Answer">{(fieldId) => <textarea id={fieldId} value={faq.answer} onChange={(event) => setFaqs((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, answer: event.target.value } : item))} />}</Field>
        <button type="button" onClick={() => setFaqs((items) => items.filter((_, itemIndex) => itemIndex !== index))}>Remove FAQ</button>
      </div>)}
      <button type="button" onClick={() => setFaqs((items) => [...items, { question: "", answer: "" }])}>Add FAQ</button>
    </section>
    <KnowledgeSources assistantId={assistantId} maxChars={config.knowledge?.max_digest_chars ?? 24000} />
  </>;
}
