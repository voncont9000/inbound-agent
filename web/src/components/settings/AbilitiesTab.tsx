import { useState } from "react";
import type { AssistantConfig } from "../../lib/config";
import { followLabel, keyFromLabel } from "../../lib/keys";
import { CalendarConnection } from "../ChannelConnections";
import { PostActionsEditor } from "../PostActionsEditor";
import { AbilityCard, Field, MoreOptions, type SettingsTabProps } from "./fields";
import { invalidProps, hasErrorUnder } from "./errors";

type BookingConfig = NonNullable<AssistantConfig["booking"]>;
type LiveAction = AssistantConfig["live_actions"][number];

const bookingNumbers = [
  ["min_notice_hours", "Minimum notice (hours)", 0, undefined],
  ["horizon_days", "Booking horizon (days)", 1, 60],
  ["buffer_minutes", "Buffer between appointments (minutes)", 0, undefined],
  ["slot_step_minutes", "Slot step (minutes)", 5, 240],
  ["max_slots_offered", "Maximum slots offered", 1, 5],
] as const;

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

const BOOKING_TIMING_PATHS = ["booking.calendar_id", "booking.check_message", "booking.book_message", "booking.send_updates", ...bookingNumbers.map(([key]) => `booking.${key}`)];

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
  const [openAbility, setOpenAbility] = useState("booking");
  const postActionsOn = actionsOpen || Boolean(config.post_actions?.length);
  const abilities = [
    { id: "booking", title: "Calendar booking", enabled: Boolean(config.booking), path: "M8 2v4m8-4v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14H3V6a2 2 0 0 1 2-2z" },
    { id: "forwarding", title: "Call forwarding", enabled: config.live_actions.includes("transfer_call"), path: "M5 12h14m-6-6 6 6-6 6M4 5v14" },
    { id: "post-actions", title: "After-conversation actions", enabled: postActionsOn, path: "M4 5h16M4 12h16M4 19h10m2-2 2 2 4-4" },
  ];
  return <>
    <p className="help abilities-intro">Turn on what this assistant may do. Everything stays off until you switch it on, and the assistant never offers an ability that is off. Each ability keeps all of its options in its own card.</p>
    <div className="abilities-grid">
      {abilities.map(({ id, title, enabled, path }) => <button
        type="button"
        key={id}
        className={`ability-tile${openAbility === id ? " ability-tile-open" : ""}`}
        aria-label={`${title}, ${enabled ? "enabled" : "disabled"}`}
        aria-pressed={openAbility === id}
        aria-expanded={openAbility === id}
        aria-controls={`ability-panel-${id}`}
        onClick={() => setOpenAbility((current) => current === id ? "" : id)}
      >
        <svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d={path} /></svg>
        <span>{title}</span>
        <span className={`ability-tile-state${enabled ? " ability-tile-enabled" : ""}`} aria-hidden="true">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={enabled ? "m5 12 4 4L19 6" : "M12 5v14M5 12h14"} /></svg>
        </span>
      </button>)}
    </div>
    <div className="abilities-expanded">
      <div id="ability-panel-booking" hidden={openAbility !== "booking"}><BookingCard {...props} /></div>
      <div id="ability-panel-forwarding" hidden={openAbility !== "forwarding"}><ForwardingCard {...props} /></div>
      <div id="ability-panel-post-actions" hidden={openAbility !== "post-actions"}><AbilityCard id="post-actions" title="After-conversation actions" description="Send emails, notify your team, add a spreadsheet row or call a webhook when a conversation ends." enabled={postActionsOn} onToggle={(enabled) => {
      if (enabled) { setActionsOpen(true); return; }
      if (config.post_actions?.length && !confirmRemoval("Remove all after-conversation actions?")) return;
      setActionsOpen(false);
      update((draft) => { draft.post_actions = []; });
    }}>
      <PostActionsEditor assistantId={props.assistantId} config={config} update={update} saveDraft={props.saveDraft} />
      </AbilityCard></div>
    </div>
  </>;
}

function BookingCard({ assistantId, config, saved, update, saveDraft, errors }: SettingsTabProps) {
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
  }} status={missingActions ? <div className="help" role="note">Live booking also needs the availability and booking actions. <button type="button" className="button-link" onClick={() => update((draft) => { draft.live_actions = withActions(draft.live_actions, BOOKING_ACTIONS, true); })}>Turn them on</button></div> : undefined}>
    {booking && <>
      <CalendarConnection assistantId={assistantId} saveDraft={saveDraft} />
      <h3>Appointment types</h3>
      {booking.appointment_types.map((appointment, index) => {
        const base = `booking.appointment_types.${index}`;
        return <div className="inline-editor stack" key={`appointment-${index}`}>
          <div className="form-grid">
            <Field path={`${base}.label`} label="Appointment label" layout="row" error={errors[`${base}.key`] ?? errors[`${base}.label`]}>{(fieldId, describedBy) => <input id={fieldId} value={appointment.label} {...invalidProps(errors, `${base}.label`)} aria-describedby={describedBy} onChange={(event) => update((draft) => {
              if (!draft.booking) return;
              const item = draft.booking.appointment_types[index];
              item.label = event.target.value;
              item.key = followLabel({ key: item.key, label: item.label, taken: draft.booking.appointment_types.map(({ key }) => key), saved: savedKeys, fallback: "appointment" });
            })} />}</Field>
            <Field path={`${base}.duration_minutes`} label="Duration (minutes)" layout="row" error={errors[`${base}.duration_minutes`]}>{(fieldId, describedBy) => <input id={fieldId} type="number" min="1" max="480" value={appointment.duration_minutes} aria-describedby={describedBy} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.appointment_types[index].duration_minutes = Number(event.target.value); })} />}</Field>
          </div>
          <Field path={`${base}.when_to_use`} label="When to use this appointment type" error={errors[`${base}.when_to_use`]}>{(fieldId, describedBy) => <textarea id={fieldId} {...invalidProps(errors, `${base}.when_to_use`)} aria-describedby={describedBy} value={appointment.when_to_use} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.appointment_types[index].when_to_use = event.target.value; })} />}</Field>
          <MoreOptions forceOpen={hasErrorUnder(errors, [`${base}.location_or_link`])}>
            <Field path={`${base}.location_or_link`} label="Location or meeting link" layout="row">{(fieldId) => <input id={fieldId} value={appointment.location_or_link ?? ""} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.appointment_types[index].location_or_link = event.target.value || null; })} />}</Field>
          </MoreOptions>
          <button type="button" disabled={booking.appointment_types.length === 1} onClick={() => update((draft) => { if (draft.booking) draft.booking.appointment_types.splice(index, 1); })}>Remove appointment type</button>
        </div>;
      })}
      <button type="button" onClick={() => update((draft) => {
        if (!draft.booking) return;
        draft.booking.appointment_types.push({ key: keyFromLabel("Appointment", draft.booking.appointment_types.map(({ key }) => key), "appointment"), label: "Appointment", duration_minutes: 30, when_to_use: "", location_or_link: null });
      })}>Add appointment type</button>
      <MoreOptions label="Booking timing and messages" forceOpen={hasErrorUnder(errors, BOOKING_TIMING_PATHS)}>
          <div className="form-grid">
            <Field path="booking.calendar_id" label="Calendar ID" layout="row" help="Use primary for the connected Google account's primary calendar.">{(fieldId, describedBy) => <input id={fieldId} value={booking.calendar_id} aria-describedby={describedBy} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.calendar_id = event.target.value; })} />}</Field>
            <Field path="booking.check_message" label="Availability holding message" layout="row">{(fieldId) => <input id={fieldId} value={booking.check_message} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.check_message = event.target.value; })} />}</Field>
            <Field path="booking.book_message" label="Booking holding message" layout="row">{(fieldId) => <input id={fieldId} value={booking.book_message} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.book_message = event.target.value; })} />}</Field>
            {bookingNumbers.map(([key, label, min, max]) => <Field key={key} path={`booking.${key}`} label={label} layout="row" error={errors[`booking.${key}`]}>{(fieldId, describedBy) => <input id={fieldId} type="number" min={min} max={max} value={booking[key]} {...invalidProps(errors, `booking.${key}`)} aria-describedby={describedBy} onChange={(event) => update((draft) => { if (draft.booking) draft.booking[key] = Number(event.target.value); })} />}</Field>)}
            <Field path="booking.send_updates" label="Calendar attendee updates" layout="row">{(fieldId) => <select id={fieldId} value={booking.send_updates} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.send_updates = event.target.value as BookingConfig["send_updates"]; })}>
              <option value="all">All attendees</option><option value="externalOnly">External attendees</option><option value="none">No updates</option>
            </select>}</Field>
          </div>
      </MoreOptions>
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
          <Field path={`${base}.label`} label="Name" layout="row" error={errors[`${base}.label`] ?? errors[`${base}.key`]}>{(fieldId, describedBy) => <input id={fieldId} value={target.label} aria-describedby={describedBy} onChange={(event) => update((draft) => {
            const item = draft.transfer_targets![index];
            item.label = event.target.value;
            item.key = followLabel({ key: item.key, label: item.label, taken: draft.transfer_targets!.map(({ key }) => key), saved: savedKeys, fallback: "target" });
          })} />}</Field>
          <Field path={`${base}.phone`} label="Phone number" layout="row" error={errors[`${base}.phone`]}>{(fieldId, describedBy) => <input id={fieldId} type="tel" value={target.phone} {...invalidProps(errors, `${base}.phone`)} aria-describedby={describedBy} onChange={(event) => update((draft) => { draft.transfer_targets![index].phone = event.target.value; })} />}</Field>
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
    <MoreOptions label="Forwarding messages">
        <Field path="transfer_handoff_message" label="Handoff message">{(fieldId) => <textarea id={fieldId} value={config.transfer_handoff_message} onChange={(event) => update((draft) => { draft.transfer_handoff_message = event.target.value; })} />}</Field>
        <Field path="transfer_failed_message" label="Message when nobody answers">{(fieldId) => <textarea id={fieldId} value={config.transfer_failed_message} onChange={(event) => update((draft) => { draft.transfer_failed_message = event.target.value; })} />}</Field>
    </MoreOptions>
  </AbilityCard>;
}
