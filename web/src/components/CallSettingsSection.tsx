import type { ReactNode } from "react";
import type { AssistantConfig } from "../lib/config";

type CallSettings = NonNullable<AssistantConfig["call_settings"]>;
type RecordingSettings = NonNullable<AssistantConfig["recording"]>;

type Props = {
  config: AssistantConfig;
  update: (change: (draft: AssistantConfig) => void) => void;
  errors: Record<string, string>;
};

const DEFAULT_CALL_SETTINGS: CallSettings = {
  max_call_minutes: null,
  max_call_message:
    "We've reached the time limit for this call, so I'll need to end it now. Thank you for calling. Goodbye.",
  silence_timeout_seconds: null,
  still_there_prompt: "Are you still there?",
  silence_goodbye_message: "I'll end the call now. Thank you for calling. Goodbye.",
  speech_speed: 1,
  interruption_sensitivity: "normal",
  uninterruptible_greeting: false,
  technical_terms: [],
};

const DEFAULT_RECORDING: RecordingSettings = {
  enabled: false,
  consent_message: "This call may be recorded for quality and training purposes.",
  retention_days: 30,
};

function Field({
  path,
  label,
  help,
  error,
  children,
}: {
  path: string;
  label: string;
  help?: string;
  error?: string;
  children: (id: string, describedBy?: string) => ReactNode;
}) {
  const id = `field-${path}`;
  const errorId = `${id}-error`;
  const helpId = `${id}-help`;
  const describedBy = [error ? errorId : "", help ? helpId : ""].filter(Boolean).join(" ") || undefined;
  return <div className="field">
    <label htmlFor={id}>{label}</label>
    {children(id, describedBy)}
    {help && <span id={helpId} className="help">{help}</span>}
    {error && <p id={errorId} className="field-error">{error}</p>}
  </div>;
}

export function CallSettingsSection({ config, update, errors }: Props) {
  const call = config.call_settings ?? DEFAULT_CALL_SETTINGS;
  const recording = config.recording ?? DEFAULT_RECORDING;
  const setCall = <K extends keyof CallSettings>(key: K, value: CallSettings[K]) => {
    update((draft) => {
      draft.call_settings ??= structuredClone(DEFAULT_CALL_SETTINGS);
      draft.call_settings[key] = value;
    });
  };
  const setRecording = <K extends keyof RecordingSettings>(key: K, value: RecordingSettings[K]) => {
    update((draft) => {
      draft.recording ??= structuredClone(DEFAULT_RECORDING);
      draft.recording[key] = value;
    });
  };
  const numberField = (
    path: keyof CallSettings,
    label: string,
    value: number | null,
    min: number,
    max: number,
    help: string,
  ) => <Field path={`call_settings.${path}`} label={label} help={help} error={errors[`call_settings.${path}`]}>
    {(id, describedBy) => <input
      id={id}
      type="number"
      min={min}
      max={max}
      step={path === "silence_timeout_seconds" ? 0.1 : 1}
      value={value ?? ""}
      aria-invalid={Boolean(errors[`call_settings.${path}`])}
      aria-describedby={describedBy}
      onChange={(event) => setCall(path, event.target.value === "" ? null : Number(event.target.value))}
    />}
  </Field>;

  return <>
    <section className="panel stack">
      <h2>Call settings</h2>
      <p className="help">Optional limits and voice behavior for this assistant. Defaults preserve current call behavior.</p>
      <div className="form-grid">
        {numberField("max_call_minutes", "Maximum call length (minutes)", call.max_call_minutes ?? null, 1, 240, "Leave blank for no time limit.")}
        {numberField("silence_timeout_seconds", "Silence timeout (seconds)", call.silence_timeout_seconds ?? null, 0.1, 60, "Leave blank to use the existing 8-second default.")}
        <Field path="call_settings.speech_speed" label={`Speech speed (${call.speech_speed.toFixed(2)}×)`} help="Speechify uses relative prosody rates to adjust speed." error={errors["call_settings.speech_speed"]}>
          {(id, describedBy) => <input id={id} type="range" min="0.7" max="1.2" step="0.05" value={call.speech_speed} aria-invalid={Boolean(errors["call_settings.speech_speed"])} aria-describedby={describedBy} onChange={(event) => setCall("speech_speed", Number(event.target.value))} />}
        </Field>
        <Field path="call_settings.interruption_sensitivity" label="Interruption sensitivity" error={errors["call_settings.interruption_sensitivity"]} help={
          call.interruption_sensitivity === "low"
            ? "Waits for more speech before interrupting; short confirmations still work while the assistant is silent."
            : call.interruption_sensitivity === "high"
              ? "Responds quickly to speech and caller interruptions."
              : "Balanced speech detection and interruptions."
        }>
          {(id, describedBy) => <select id={id} value={call.interruption_sensitivity} aria-invalid={Boolean(errors["call_settings.interruption_sensitivity"])} aria-describedby={describedBy} onChange={(event) => setCall("interruption_sensitivity", event.target.value as CallSettings["interruption_sensitivity"])}>
            <option value="low">Low</option><option value="normal">Normal</option><option value="high">High</option>
          </select>}
        </Field>
      </div>
      {([
        ["still_there_prompt", "Still-there prompt"],
        ["silence_goodbye_message", "Silence goodbye message"],
        ["max_call_message", "Maximum-call message"],
      ] as const).map(([key, label]) => <Field key={key} path={`call_settings.${key}`} label={label} error={errors[`call_settings.${key}`]}>
        {(id, describedBy) => <textarea id={id} value={call[key]} aria-invalid={Boolean(errors[`call_settings.${key}`])} aria-describedby={describedBy} onChange={(event) => setCall(key, event.target.value)} />}
      </Field>)}
      <label className="check-row">
        <input type="checkbox" checked={call.uninterruptible_greeting || recording.enabled} disabled={recording.enabled} onChange={(event) => setCall("uninterruptible_greeting", event.target.checked)} />
        <span><strong>Uninterruptible greeting</strong> — callers can't interrupt the first greeting.{recording.enabled && " Always on while recording is enabled."}</span>
      </label>
      <Field path="call_settings.technical_terms" label="Technical terms" help="One term per line; terms are passed to speech recognition.">
        {(id, describedBy) => <textarea id={id} value={(call.technical_terms ?? []).join("\n")} aria-describedby={describedBy} onChange={(event) => setCall("technical_terms", event.target.value.split("\n"))} />}
      </Field>
    </section>
    <section className="panel stack">
      <h2>Call recording</h2>
      <label className="check-row">
        <input type="checkbox" checked={recording.enabled} onChange={(event) => setRecording("enabled", event.target.checked)} />
        <span><strong>Record calls</strong> — calls are recorded only when this is enabled.</span>
      </label>
      {recording.enabled && <>
        <Field path="recording.consent_message" label="Recording consent message" error={errors["recording.consent_message"]}>
          {(id, describedBy) => <textarea id={id} value={recording.consent_message} aria-invalid={Boolean(errors["recording.consent_message"])} aria-describedby={describedBy} onChange={(event) => setRecording("consent_message", event.target.value)} />}
        </Field>
        <Field path="recording.retention_days" label="Retention (days)" error={errors["recording.retention_days"]}>
          {(id, describedBy) => <input id={id} type="number" min="1" max="365" value={recording.retention_days} aria-invalid={Boolean(errors["recording.retention_days"])} aria-describedby={describedBy} onChange={(event) => setRecording("retention_days", Number(event.target.value))} />}
        </Field>
      </>}
    </section>
  </>;
}
