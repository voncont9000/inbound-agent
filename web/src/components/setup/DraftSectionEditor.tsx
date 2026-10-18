import { Badge } from "../States";
import type { Schemas } from "../../api/client";

export type DraftSectionName = keyof Schemas["SetupDraft"];

const weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

function ServiceIntakeFields({ fields, heading = true }: { fields: Schemas["FieldSpec"][]; heading?: boolean }) {
  return <div>
    {heading && <p className="field-label">Intake fields</p>}
    {fields.length ? <ul>{fields.map((field) => <li key={field.name}>
      {field.label}{field.critical && <> <Badge tone="warning">Critical</Badge></>}
    </li>)}</ul> : <p className="muted">No intake fields suggested.</p>}
  </div>;
}

export function DraftSectionEditor({
  section,
  value,
  onChange,
}: {
  section: DraftSectionName;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  if (section === "company_info") {
    return <div className="field">
      <label htmlFor="setup-company-info">Company information</label>
      <textarea id="setup-company-info" value={value as string} onChange={(event) => onChange(event.target.value)} />
    </div>;
  }
  if (section === "faqs") {
    const faqs = value as Schemas["KnowledgeFAQ"][];
    return <div className="stack">
      {faqs.map((faq, index) => <div className="inline-editor" key={index}>
        <div className="field">
          <label htmlFor={`setup-faq-question-${index}`}>Question {index + 1}</label>
          <input id={`setup-faq-question-${index}`} value={faq.question} onChange={(event) => onChange(faqs.map((item, itemIndex) => itemIndex === index ? { ...item, question: event.target.value } : item))} />
        </div>
        <div className="field">
          <label htmlFor={`setup-faq-answer-${index}`}>Answer {index + 1}</label>
          <textarea id={`setup-faq-answer-${index}`} value={faq.answer} onChange={(event) => onChange(faqs.map((item, itemIndex) => itemIndex === index ? { ...item, answer: event.target.value } : item))} />
        </div>
        <button type="button" onClick={() => onChange(faqs.filter((_, itemIndex) => itemIndex !== index))}>Remove question {index + 1}</button>
      </div>)}
      <button type="button" onClick={() => onChange([...faqs, { question: "", answer: "" }])}>Add question</button>
    </div>;
  }
  if (section === "office_hours") {
    const hours = value as Schemas["OfficeHours"];
    return <div className="stack">
      <fieldset className="stack">
        <legend>Open days</legend>
        {weekdays.map((day) => <label className="check-row" key={day}>
          <input type="checkbox" checked={hours.days.includes(day)} onChange={(event) => onChange({ ...hours, days: event.target.checked ? [...hours.days, day] : hours.days.filter((item) => item !== day) })} />
          <span>{day}</span>
        </label>)}
      </fieldset>
      <div className="form-grid">
        <div className="field"><label htmlFor="setup-opens">Opens</label><input id="setup-opens" type="time" value={hours.opens} onChange={(event) => onChange({ ...hours, opens: event.target.value })} /></div>
        <div className="field"><label htmlFor="setup-closes">Closes</label><input id="setup-closes" type="time" value={hours.closes} onChange={(event) => onChange({ ...hours, closes: event.target.value })} /></div>
      </div>
      <div className="field"><label htmlFor="setup-timezone">Timezone</label><input id="setup-timezone" value={hours.timezone} onChange={(event) => onChange({ ...hours, timezone: event.target.value })} /></div>
    </div>;
  }
  if (section === "price_list") {
    const prices = value as Schemas["PriceItem"][];
    return <div className="stack">
      {prices.map((item, index) => <div className="inline-editor" key={index}>
        <div className="field"><label htmlFor={`setup-price-service-${index}`}>Service {index + 1}</label><input id={`setup-price-service-${index}`} value={item.service} onChange={(event) => onChange(prices.map((entry, itemIndex) => itemIndex === index ? { ...entry, service: event.target.value } : entry))} /></div>
        <div className="field"><label htmlFor={`setup-price-value-${index}`}>Price {index + 1}</label><input id={`setup-price-value-${index}`} value={item.price} onChange={(event) => onChange(prices.map((entry, itemIndex) => itemIndex === index ? { ...entry, price: event.target.value } : entry))} /></div>
        <div className="field"><label htmlFor={`setup-price-notes-${index}`}>Notes {index + 1}</label><textarea id={`setup-price-notes-${index}`} value={item.notes} onChange={(event) => onChange(prices.map((entry, itemIndex) => itemIndex === index ? { ...entry, notes: event.target.value } : entry))} /></div>
        <button type="button" onClick={() => onChange(prices.filter((_, itemIndex) => itemIndex !== index))}>Remove price {index + 1}</button>
      </div>)}
      <button type="button" onClick={() => onChange([...prices, { service: "", price: "", notes: "" }])}>Add price</button>
    </div>;
  }
  const services = value as Record<string, Schemas["ServiceConfig"]>;
  return <div className="stack">
    {Object.entries(services).map(([key, service]) => <div className="inline-editor" key={key}>
      <div className="field"><label htmlFor={`setup-service-${key}`}>Service name</label><input id={`setup-service-${key}`} value={service.label} onChange={(event) => onChange({ ...services, [key]: { ...service, label: event.target.value } })} /></div>
      <ServiceIntakeFields fields={service.fields} />
      <button type="button" onClick={() => {
        const updated = { ...services };
        delete updated[key];
        onChange(updated);
      }}>Remove {service.label}</button>
    </div>)}
    {!Object.keys(services).length && <p className="muted">No services remain in this draft.</p>}
  </div>;
}

export function DraftSectionPreview({ section, value }: { section: DraftSectionName; value: unknown }) {
  if (section === "company_info") return <p className="context-card">{value as string}</p>;
  if (section === "faqs") {
    const faqs = value as Schemas["KnowledgeFAQ"][];
    return faqs.length ? <ul>{faqs.map((faq, index) => <li key={index}><strong>{faq.question}</strong><br />{faq.answer}</li>)}</ul> : <p className="muted">No questions in this draft.</p>;
  }
  if (section === "office_hours") {
    const hours = value as Schemas["OfficeHours"];
    return <p>{hours.days.join(", ")} · {hours.opens}–{hours.closes} · {hours.timezone}</p>;
  }
  if (section === "price_list") {
    const prices = value as Schemas["PriceItem"][];
    return prices.length ? <ul>{prices.map((item, index) => <li key={index}><strong>{item.service}: {item.price}</strong>{item.notes && ` — ${item.notes}`}</li>)}</ul> : <p className="muted">No published prices in this draft.</p>;
  }
  const services = value as Record<string, Schemas["ServiceConfig"]>;
  return Object.keys(services).length ? <ul>{Object.entries(services).map(([key, service]) => <li key={key}>
    <strong>{service.label}</strong>
    <ServiceIntakeFields fields={service.fields} heading={false} />
  </li>)}</ul> : <p className="muted">No services in this draft.</p>;
}
