import { useMemo } from "react";
import type { AssistantConfig } from "../../lib/config";
import { enumAt, rangeAt } from "../../lib/configSchema";
import { CallSettingsSection } from "../CallSettingsSection";
import { Field, type SettingsTabProps } from "./fields";
import { invalidProps } from "./errors";

type LiveAction = AssistantConfig["live_actions"][number];

/** Actions owned by an ability card (booking, forwarding, readback) are switched on there. */
const ABILITY_ACTIONS: LiveAction[] = ["check_availability", "book_appointment", "transfer_call", "confirm_critical_fields"];

const liveActionHelp: Record<string, string> = {
  flag_urgent: "Flag urgent concerns for the team.",
  request_callback: "Create a callback request.",
  log_complaint: "Record a complaint for the team.",
  end_call: "End the interaction after a clear closing.",
};

export function AdvancedTab({ config, update, errors, schema }: SettingsTabProps) {
  const actionOptions = useMemo(() => enumAt(schema, ["properties", "live_actions", "items", "enum"], Object.keys(liveActionHelp)).filter((action) => !ABILITY_ACTIONS.includes(action as LiveAction)), [schema]);
  const thresholdRange = useMemo(() => rangeAt(schema), [schema]);
  return <>
    <p className="help">Technical settings with template defaults. Most assistants never need to change these.</p>
    <section className="panel stack" aria-labelledby="live-actions-heading"><h2 id="live-actions-heading">Live actions</h2><p className="help">Extra tools the assistant may use during a live call. They add latency and default to none; booking, forwarding and readback actions are switched on from Abilities.</p>
      {actionOptions.map((action) => <label className="check-row" key={action}><input type="checkbox" checked={config.live_actions.includes(action as LiveAction)} onChange={(event) => update((draft) => { const value = action as LiveAction; draft.live_actions = event.target.checked ? [...draft.live_actions, value] : draft.live_actions.filter((item) => item !== value); })} /><span><strong>{action.replaceAll("_", " ")}</strong> — {liveActionHelp[action] ?? "Available during a live interaction."}</span></label>)}
    </section>
    <section className="panel stack" aria-labelledby="review-heading"><h2 id="review-heading">Review threshold</h2>
      <Field path="review_confidence_threshold" label={`Confidence threshold: ${config.review_confidence_threshold.toFixed(2)}`} error={errors.review_confidence_threshold} help="Fields extracted below this confidence go to the review queue">{(fieldId, describedBy) => <input id={fieldId} type="range" min={thresholdRange.min} max={thresholdRange.max} step="0.05" value={config.review_confidence_threshold} {...invalidProps(errors, "review_confidence_threshold")} aria-describedby={describedBy} onChange={(event) => update((draft) => { draft.review_confidence_threshold = Number(event.target.value); })} />}</Field>
    </section>
    <CallSettingsSection config={config} update={update} errors={errors} />
  </>;
}
