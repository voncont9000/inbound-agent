import { useMemo, useState } from "react";
import type { AssistantConfig } from "../../lib/config";
import { enumAt, rangeAt } from "../../lib/configSchema";
import { CallSettingsSection } from "../CallSettingsSection";
import { ManualChannelForm } from "../ChannelConnections";
import { Field, type SettingsTabProps } from "./fields";
import { invalidProps } from "./errors";

type BookingConfig = NonNullable<AssistantConfig["booking"]>;
type LiveAction = AssistantConfig["live_actions"][number];

const liveActionHelp: Record<string, string> = {
  confirm_critical_fields: "Prompt the caller to confirm critical details.",
  flag_urgent: "Flag urgent concerns for the team.",
  request_callback: "Create a callback request.",
  log_complaint: "Record a complaint for the team.",
  end_call: "End the interaction after a clear closing.",
  check_availability: "Find real Calendar openings; requires booking configuration and Calendar access.",
  book_appointment: "Create a confirmed Calendar event after the caller chooses and confirms a slot.",
  transfer_call: "Transfer the caller to a configured person; needs at least one transfer target.",
};

const bookingNumbers = [
  ["min_notice_hours", "Minimum notice (hours)", 0, undefined],
  ["horizon_days", "Booking horizon (days)", 1, 60],
  ["buffer_minutes", "Buffer between appointments (minutes)", 0, undefined],
  ["slot_step_minutes", "Slot step (minutes)", 5, 240],
  ["max_slots_offered", "Maximum slots offered", 1, 5],
] as const;

export function AdvancedTab({ assistantId, config, update, errors, schema }: SettingsTabProps) {
  const [phraseText, setPhraseText] = useState<Record<string, string>>({});
  const readbackOptions = useMemo(() => enumAt(schema, ["properties", "readback", "enum"], ["off", "email", "critical"]), [schema]);
  const actionOptions = useMemo(() => enumAt(schema, ["properties", "live_actions", "items", "enum"], Object.keys(liveActionHelp)), [schema]);
  const thresholdRange = useMemo(() => rangeAt(schema), [schema]);
  const serviceKeys = Object.keys(config.services);
  const booking = config.booking;
  return <>
    <p className="help">Technical settings with template defaults. Most assistants never need to change these.</p>
    <section className="panel stack" aria-labelledby="readback-mode-heading"><h2 id="readback-mode-heading">Readback mode</h2>
      <p className="help">Choose which information the assistant reads back for caller confirmation.</p>
      {readbackOptions.map((option) => <label key={option} className="radio-row"><input type="radio" name="readback" value={option} checked={config.readback === option} onChange={() => update((draft) => { draft.readback = option as AssistantConfig["readback"]; })} /><span><strong>{option}</strong> — {option === "off" ? "Don't read details back to the caller." : option === "email" ? "Read email addresses back letter by letter so the caller can confirm them." : "Read back every field marked critical (for example name, phone, email or key dates) and ask the caller to confirm."}</span></label>)}
    </section>
    <section className="panel stack" aria-labelledby="live-actions-heading"><h2 id="live-actions-heading">Live actions</h2><p className="help">Live actions add latency and default to none. Ability switches turn the matching actions on for you.</p>
      {actionOptions.map((action) => <label className="check-row" key={action}><input type="checkbox" checked={config.live_actions.includes(action as LiveAction)} onChange={(event) => update((draft) => { const value = action as LiveAction; draft.live_actions = event.target.checked ? [...draft.live_actions, value] : draft.live_actions.filter((item) => item !== value); })} /><span><strong>{action.replaceAll("_", " ")}</strong> — {liveActionHelp[action] ?? "Available during a live interaction."}</span></label>)}
    </section>
    <section className="panel stack" aria-labelledby="urgency-heading"><h2 id="urgency-heading">Urgency rules</h2>
      {config.urgency_rules.map((rule, index) => <section className="inline-editor" key={`urgency-${index}`}>
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
      </section>)}
      <button type="button" onClick={() => update((draft) => { draft.urgency_rules.push({ type: "keyword", phrases: [], reason: "" }); })}>Add urgency rule</button>
    </section>
    <section className="panel stack" aria-labelledby="review-heading"><h2 id="review-heading">Review threshold</h2>
      <Field path="review_confidence_threshold" label={`Confidence threshold: ${config.review_confidence_threshold.toFixed(2)}`} error={errors.review_confidence_threshold} help="Fields extracted below this confidence go to the review queue">{(fieldId, describedBy) => <input id={fieldId} type="range" min={thresholdRange.min} max={thresholdRange.max} step="0.05" value={config.review_confidence_threshold} {...invalidProps(errors, "review_confidence_threshold")} aria-describedby={describedBy} onChange={(event) => update((draft) => { draft.review_confidence_threshold = Number(event.target.value); })} />}</Field>
    </section>
    <section className="panel stack" aria-labelledby="booking-advanced-heading"><h2 id="booking-advanced-heading">Booking timing</h2>
      {!booking && <p className="muted">Turn on Calendar booking under Abilities to change these.</p>}
      {booking && <div className="form-grid">
        <Field path="booking.calendar_id" label="Calendar ID" help="Use primary for the connected Google account's primary calendar.">{(fieldId, describedBy) => <input id={fieldId} value={booking.calendar_id} aria-describedby={describedBy} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.calendar_id = event.target.value; })} />}</Field>
        <Field path="booking.check_message" label="Availability holding message">{(fieldId) => <input id={fieldId} value={booking.check_message} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.check_message = event.target.value; })} />}</Field>
        <Field path="booking.book_message" label="Booking holding message">{(fieldId) => <input id={fieldId} value={booking.book_message} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.book_message = event.target.value; })} />}</Field>
        {bookingNumbers.map(([key, label, min, max]) => <Field key={key} path={`booking.${key}`} label={label} error={errors[`booking.${key}`]}>{(fieldId, describedBy) => <input id={fieldId} type="number" min={min} max={max} value={booking[key]} {...invalidProps(errors, `booking.${key}`)} aria-describedby={describedBy} onChange={(event) => update((draft) => { if (draft.booking) draft.booking[key] = Number(event.target.value); })} />}</Field>)}
        <Field path="booking.send_updates" label="Calendar attendee updates">{(fieldId) => <select id={fieldId} value={booking.send_updates} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.send_updates = event.target.value as BookingConfig["send_updates"]; })}>
          <option value="all">All attendees</option><option value="externalOnly">External attendees</option><option value="none">No updates</option>
        </select>}</Field>
      </div>}
    </section>
    <section className="panel stack" aria-labelledby="transfer-messages-heading"><h2 id="transfer-messages-heading">Call forwarding messages</h2>
      <Field path="transfer_handoff_message" label="Handoff message">{(fieldId) => <textarea id={fieldId} value={config.transfer_handoff_message} onChange={(event) => update((draft) => { draft.transfer_handoff_message = event.target.value; })} />}</Field>
      <Field path="transfer_failed_message" label="Message when nobody answers">{(fieldId) => <textarea id={fieldId} value={config.transfer_failed_message} onChange={(event) => update((draft) => { draft.transfer_failed_message = event.target.value; })} />}</Field>
    </section>
    <CallSettingsSection config={config} update={update} errors={errors} />
    <ManualChannelForm assistantId={assistantId} />
  </>;
}
