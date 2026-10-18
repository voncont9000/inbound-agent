import { useMemo, useState } from "react";
import type { Schemas } from "../../api/client";
import type { AssistantConfig } from "../../lib/config";
import { enumAt } from "../../lib/configSchema";
import { followLabel, keyFromLabel } from "../../lib/keys";
import { CalendarConnection } from "../ChannelConnections";
import { PostActionsEditor } from "../PostActionsEditor";
import { AbilityCard, Field, MoreOptions, type SettingsTabProps } from "./fields";
import { invalidProps, hasErrorUnder } from "./errors";

type FieldSpec = Schemas["FieldSpec"];
type BookingConfig = NonNullable<AssistantConfig["booking"]>;
type LiveAction = AssistantConfig["live_actions"][number];

const DEFAULT_BOOKING_CONFIG: BookingConfig = {
  calendar_id: "primary",
  check_message: "Let me check the diary.",
  book_message: "One moment while I book that.",
  appointment_types: [{
    key: "consultation",
    label: "Consultation",
    duration_minutes: 30,
    when_to_use: "For callers requesting an initial consultation",
    location_or_link: null,
  }],
  min_notice_hours: 2,
  horizon_days: 14,
  buffer_minutes: 0,
  max_slots_offered: 3,
  slot_step_minutes: 30,
  send_updates: "all",
};

const BOOKING_ACTIONS: LiveAction[] = ["check_availability", "book_appointment"];

function withActions(actions: LiveAction[], added: LiveAction[], enabled: boolean) {
  const rest = actions.filter((action) => !added.includes(action));
  return enabled ? [...rest, ...added] : rest;
}

function confirmRemoval(message: string) {
  return typeof window.confirm !== "function" || window.confirm(message);
}

export function AbilitiesTab(props: SettingsTabProps) {
  const { config, update } = props;
  const [actionsOpen, setActionsOpen] = useState(false);
  const postActionsOn = actionsOpen || Boolean(config.post_actions?.length);
  return <>
    <p className="help">Turn on what this assistant may do. Everything stays off until you switch it on, and the assistant never offers an ability that is off.</p>
    <BookingCard {...props} />
    <ForwardingCard {...props} />
    <AbilityCard id="post-actions" title="After-conversation actions" description="Send emails, notify your team, add a spreadsheet row or call a webhook when a conversation ends." enabled={postActionsOn} onToggle={(enabled) => {
      if (enabled) { setActionsOpen(true); return; }
      if (config.post_actions?.length && !confirmRemoval("Remove all after-conversation actions?")) return;
      setActionsOpen(false);
      update((draft) => { draft.post_actions = []; });
    }}>
      <PostActionsEditor assistantId={props.assistantId} config={config} update={update} />
    </AbilityCard>
    <AbilityCard id="readback" title="Readback" description="Read important details back to the caller so they can confirm them. Choose exactly what is read back under Advanced." enabled={config.readback !== "off"} onToggle={(enabled) => update((draft) => { draft.readback = enabled ? "critical" : "off"; })}>
      <p className="muted">Reading back: {config.readback === "email" ? "email addresses, letter by letter" : "every field marked critical"}.</p>
    </AbilityCard>
    <ServicesCard {...props} />
  </>;
}

function BookingCard({ assistantId, config, saved, update, errors }: SettingsTabProps) {
  const booking = config.booking;
  const savedKeys = (saved.booking?.appointment_types ?? []).map(({ key }) => key);
  const missingActions = booking && BOOKING_ACTIONS.some((action) => !config.live_actions.includes(action));
  return <AbilityCard id="booking" title="Calendar booking" description="Offer real open slots from your calendar and book appointments." enabled={Boolean(booking)} onToggle={(enabled) => {
    if (
      !enabled &&
      booking?.appointment_types.length &&
      !confirmRemoval(
        `Turning off calendar booking removes its ${booking.appointment_types.length} appointment type(s). Continue?`,
      )
    ) {
      return;
    }
    update((draft) => {
      draft.booking = enabled ? structuredClone(DEFAULT_BOOKING_CONFIG) : null;
      draft.live_actions = withActions(draft.live_actions, BOOKING_ACTIONS, enabled);
    });
  }} status={missingActions ? <p className="help" role="note">Live booking also needs the availability and booking live actions under Advanced.</p> : undefined}>
    {booking && <>
      <CalendarConnection assistantId={assistantId} />
      <h3>Appointment types</h3>
      {booking.appointment_types.map((appointment, index) => {
        const base = `booking.appointment_types.${index}`;
        return <div className="inline-editor stack" key={`appointment-${index}`}>
          <div className="form-grid">
            <Field path={`${base}.label`} label="Appointment label" error={errors[`${base}.key`] ?? errors[`${base}.label`]}>{(fieldId, describedBy) => <input id={fieldId} value={appointment.label} {...invalidProps(errors, `${base}.label`)} aria-describedby={describedBy} onChange={(event) => update((draft) => {
              if (!draft.booking) return;
              const item = draft.booking.appointment_types[index];
              item.label = event.target.value;
              item.key = followLabel({ key: item.key, label: item.label, taken: draft.booking.appointment_types.map(({ key }) => key), saved: savedKeys, fallback: "appointment" });
            })} />}</Field>
            <Field path={`${base}.duration_minutes`} label="Duration (minutes)" error={errors[`${base}.duration_minutes`]}>{(fieldId, describedBy) => <input id={fieldId} type="number" min="1" max="480" value={appointment.duration_minutes} aria-describedby={describedBy} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.appointment_types[index].duration_minutes = Number(event.target.value); })} />}</Field>
          </div>
          <Field path={`${base}.when_to_use`} label="When to use this appointment type" error={errors[`${base}.when_to_use`]}>{(fieldId, describedBy) => <textarea id={fieldId} {...invalidProps(errors, `${base}.when_to_use`)} aria-describedby={describedBy} value={appointment.when_to_use} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.appointment_types[index].when_to_use = event.target.value; })} />}</Field>
          <MoreOptions forceOpen={hasErrorUnder(errors, [`${base}.location_or_link`])}>
            <Field path={`${base}.location_or_link`} label="Location or meeting link">{(fieldId) => <input id={fieldId} value={appointment.location_or_link ?? ""} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.appointment_types[index].location_or_link = event.target.value || null; })} />}</Field>
          </MoreOptions>
          <button type="button" disabled={booking.appointment_types.length === 1} onClick={() => update((draft) => { if (draft.booking) draft.booking.appointment_types.splice(index, 1); })}>Remove appointment type</button>
        </div>;
      })}
      <button type="button" onClick={() => update((draft) => {
        if (!draft.booking) return;
        draft.booking.appointment_types.push({ key: keyFromLabel("Appointment", draft.booking.appointment_types.map(({ key }) => key), "appointment"), label: "Appointment", duration_minutes: 30, when_to_use: "", location_or_link: null });
      })}>Add appointment type</button>
    </>}
  </AbilityCard>;
}

function ForwardingCard({ config, saved, update, errors }: SettingsTabProps) {
  const targets = config.transfer_targets ?? [];
  const savedKeys = (saved.transfer_targets ?? []).map(({ key }) => key);
  const enabled = config.live_actions.includes("transfer_call");
  return <AbilityCard id="forwarding" title="Call forwarding" description="Transfer callers to a person on your team when they ask for one." enabled={enabled} onToggle={(on) => update((draft) => {
    draft.live_actions = withActions(draft.live_actions, ["transfer_call"], on);
    draft.transfer_targets ??= [];
  })} status={enabled && !targets.length ? <p className="help" role="note">Add at least one person to forward calls to.</p> : undefined}>
    {targets.map((target, index) => {
      const base = `transfer_targets.${index}`;
      return <div className="inline-editor stack" key={`transfer-${index}`}>
        <div className="form-grid">
          <Field path={`${base}.label`} label="Name" error={errors[`${base}.label`] ?? errors[`${base}.key`]}>{(fieldId, describedBy) => <input id={fieldId} value={target.label} aria-describedby={describedBy} onChange={(event) => update((draft) => {
            const item = draft.transfer_targets![index];
            item.label = event.target.value;
            item.key = followLabel({ key: item.key, label: item.label, taken: draft.transfer_targets!.map(({ key }) => key), saved: savedKeys, fallback: "target" });
          })} />}</Field>
          <Field path={`${base}.phone`} label="Phone number" error={errors[`${base}.phone`]}>{(fieldId, describedBy) => <input id={fieldId} type="tel" value={target.phone} {...invalidProps(errors, `${base}.phone`)} aria-describedby={describedBy} onChange={(event) => update((draft) => { draft.transfer_targets![index].phone = event.target.value; })} />}</Field>
        </div>
        <Field path={`${base}.when_to_use`} label="When to use" error={errors[`${base}.when_to_use`]}>{(fieldId, describedBy) => <textarea id={fieldId} value={target.when_to_use} aria-describedby={describedBy} onChange={(event) => update((draft) => { draft.transfer_targets![index].when_to_use = event.target.value; })} />}</Field>
        <MoreOptions>
          <label className="check-row"><input type="checkbox" checked={target.office_hours_only} onChange={(event) => update((draft) => { draft.transfer_targets![index].office_hours_only = event.target.checked; })} />Only during office hours</label>
        </MoreOptions>
        <button type="button" onClick={() => update((draft) => { draft.transfer_targets!.splice(index, 1); })}>Remove transfer target</button>
      </div>;
    })}
    <button type="button" onClick={() => update((draft) => {
      draft.transfer_targets ??= [];
      draft.transfer_targets.push({ key: keyFromLabel("", draft.transfer_targets.map(({ key }) => key), "target"), label: "", phone: "", when_to_use: "", office_hours_only: true });
    })}>Add transfer target</button>
  </AbilityCard>;
}

function renameServiceKey(draft: AssistantConfig, from: string, to: string) {
  if (from === to) return;
  draft.services = Object.fromEntries(Object.entries(draft.services).map(([key, value]) => [key === from ? to : key, value]));
  draft.urgency_rules.forEach((rule) => { if (rule.type === "date_deadline" && rule.service === from) rule.service = to; });
}

function ServicesCard({ config, saved, update, errors, schema }: SettingsTabProps) {
  const [open, setOpen] = useState(false);
  const [choiceText, setChoiceText] = useState<Record<string, string>>({});
  const typeOptions = useMemo(() => enumAt(schema, ["$defs", "FieldSpec", "properties", "type", "enum"], ["string", "email", "phone", "date", "boolean", "integer", "number", "enum", "list"]), [schema]);
  const serviceKeys = Object.keys(config.services);
  const enabled = open || serviceKeys.length > 0;
  return <AbilityCard id="services" title="Services and intake fields" description="The services you offer and the details the assistant collects for each one." enabled={enabled} onToggle={(on) => {
    if (on) { setOpen(true); return; }
    if (serviceKeys.length && !confirmRemoval("Remove all services and their intake fields?")) return;
    setOpen(false);
    update((draft) => {
      draft.services = {};
      draft.urgency_rules = draft.urgency_rules.filter((rule) => rule.type !== "date_deadline");
    });
  }}>
    {serviceKeys.map((serviceKey, serviceIndex) => {
      const service = config.services[serviceKey];
      const savedFieldNames = (saved.services[serviceKey]?.fields ?? []).map(({ name }) => name);
      return <section className="inline-editor stack" key={`service-${serviceIndex}`} aria-label={service.label || "New service"}>
        <Field path={`services.${serviceKey}.label`} label="Service label" error={errors[`services.${serviceKey}.label`]}>{(fieldId, describedBy) => <input id={fieldId} value={service.label} aria-describedby={describedBy} onChange={(event) => update((draft) => {
          const label = event.target.value;
          draft.services[serviceKey].label = label;
          const nextKey = followLabel({ key: serviceKey, label, taken: Object.keys(draft.services), saved: Object.keys(saved.services), fallback: "service" });
          renameServiceKey(draft, serviceKey, nextKey);
        })} />}</Field>
        {service.fields.map((field, fieldIndex) => {
          const base = `services.${serviceKey}.fields.${fieldIndex}`;
          return <div className="inline-editor stack" key={`field-${fieldIndex}`}>
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
  </AbilityCard>;
}
