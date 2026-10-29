import { useMemo } from "react";
import type { AssistantConfig } from "../../lib/config";
import { enumAt, rangeAt } from "../../lib/configSchema";
import { CallSettingsSection } from "../CallSettingsSection";
import { Field, type SettingsTabProps } from "./fields";
import { invalidProps } from "./errors";

type LiveAction = AssistantConfig["live_actions"][number];

/** Actions owned by an ability card are switched on there. */
const ABILITY_ACTIONS: LiveAction[] = ["check_availability", "book_appointment", "transfer_call"];
const READBACK_ACTIONS: LiveAction[] = ["confirm_critical_fields"];
const MANAGED_ACTIONS = [...ABILITY_ACTIONS, ...READBACK_ACTIONS];

const liveActionHelp: Record<string, string> = {
  flag_urgent: "Flag urgent concerns for the team.",
  request_callback: "Create a callback request.",
  log_complaint: "Record a complaint for the team.",
  end_call: "End the interaction after a clear closing.",
};

const readbackHelp: Record<string, string> = {
  email: "Read email addresses back letter by letter so the caller can confirm them.",
  critical: "Read back every field marked critical (for example name, phone, email or key dates) and ask the caller to confirm.",
};

export function AdvancedTab({ config, update, errors, schema }: SettingsTabProps) {
  const actionOptions = useMemo(() => enumAt(schema, ["properties", "live_actions", "items", "enum"], Object.keys(liveActionHelp)).filter((action) => !MANAGED_ACTIONS.includes(action as LiveAction)), [schema]);
  const readbackModes = useMemo(() => enumAt(schema, ["properties", "readback", "enum"], ["off", "email", "critical"]), [schema]);
  const thresholdRange = useMemo(() => rangeAt(schema), [schema]);
  return <>
    <p className="help">Technical settings with template defaults. Most assistants never need to change these.</p>
    <section className="panel stack" aria-labelledby="live-actions-heading"><h2 id="live-actions-heading">Live actions</h2><p className="help">Extra tools the assistant may use during a live call. They add latency and default to none; booking and forwarding actions are switched on from Abilities; readback is below.</p>
      {actionOptions.map((action) => <label className="check-row" key={action}><input type="checkbox" checked={config.live_actions.includes(action as LiveAction)} onChange={(event) => update((draft) => { const value = action as LiveAction; draft.live_actions = event.target.checked ? [...draft.live_actions, value] : draft.live_actions.filter((item) => item !== value); })} /><span><strong>{action.replaceAll("_", " ")}</strong> — {liveActionHelp[action] ?? "Available during a live interaction."}</span></label>)}
    </section>
    <section className="panel stack" aria-labelledby="readback-heading"><h2 id="readback-heading">Readback</h2>
      <fieldset className="stack readback-modes"><legend>What to read back</legend>
        {readbackModes.map((mode) => <label key={mode} className="radio-row"><input type="radio" name="readback" value={mode} checked={config.readback === mode} onChange={() => update((draft) => {
          draft.readback = mode as AssistantConfig["readback"];
          if (mode === "off") draft.live_actions = draft.live_actions.filter((action) => action !== "confirm_critical_fields");
        })} /><span>{mode === "off" ? <><strong>Off</strong> — Don&apos;t read details back.</> : readbackHelp[mode] ?? mode}</span></label>)}
      </fieldset>
      <label className="check-row"><input type="checkbox" checked={config.live_actions.includes("confirm_critical_fields")} disabled={config.readback === "off"} onChange={(event) => update((draft) => {
        draft.live_actions = event.target.checked
          ? [...draft.live_actions, "confirm_critical_fields"]
          : draft.live_actions.filter((action) => action !== "confirm_critical_fields");
      })} />Record the caller&apos;s confirmation during the call</label>
      <p className="help">Mark which intake fields are critical under General › Services and intake fields.</p>
    </section>
    <section className="panel stack" aria-labelledby="review-heading"><h2 id="review-heading">Review threshold</h2>
      <Field path="review_confidence_threshold" label={`Confidence threshold: ${config.review_confidence_threshold.toFixed(2)}`} error={errors.review_confidence_threshold} help="Fields extracted below this confidence go to the review queue">{(fieldId, describedBy) => <input id={fieldId} type="range" min={thresholdRange.min} max={thresholdRange.max} step="0.05" value={config.review_confidence_threshold} {...invalidProps(errors, "review_confidence_threshold")} aria-describedby={describedBy} onChange={(event) => update((draft) => { draft.review_confidence_threshold = Number(event.target.value); })} />}</Field>
    </section>
    <CallSettingsSection config={config} update={update} errors={errors} />
  </>;
}
