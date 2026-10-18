import { useState } from "react";
import { followLabel, humanizeKey, keyFromLabel } from "../../lib/keys";
import { KnowledgeSources } from "../KnowledgeSources";
import { Field, type SettingsTabProps } from "./fields";
import { invalidProps } from "./errors";

const days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export function KnowledgeTab({ assistantId, config, saved, update, errors }: SettingsTabProps) {
  const [contactLabels, setContactLabels] = useState<Record<number, string>>({});
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
    <section className="panel stack" aria-labelledby="price-list-heading"><h2 id="price-list-heading">Price list</h2>
      {config.price_list.map((item, index) => <div className="inline-editor" key={`price-${index}`}>
        <div className="form-grid">{(["service", "price", "notes"] as const).map((key) => <Field key={key} path={`price_list.${index}.${key}`} label={key.replaceAll("_", " ")}>{(fieldId) => key === "notes" ? <textarea id={fieldId} value={item[key]} onChange={(event) => update((draft) => { draft.price_list[index][key] = event.target.value; })} /> : <input id={fieldId} value={item[key]} onChange={(event) => update((draft) => { draft.price_list[index][key] = event.target.value; })} />}</Field>)}</div>
        <button type="button" onClick={() => update((draft) => { draft.price_list.splice(index, 1); })}>Remove price</button>
      </div>)}
      <button type="button" onClick={() => update((draft) => { draft.price_list.push({ service: "", price: "", notes: "" }); })}>Add price</button>
    </section>
    <section className="panel stack" aria-labelledby="contacts-heading"><h2 id="contacts-heading">Contact details</h2>
      <p className="help">Details the assistant may share, such as the office email or an emergency line.</p>
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
    </section>
    <KnowledgeSources assistantId={assistantId} maxChars={config.knowledge?.max_digest_chars ?? 24000} />
  </>;
}
