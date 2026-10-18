import { Field, type SettingsTabProps } from "./fields";
import { invalidProps } from "./errors";
import { VoicePicker } from "../VoicePicker";

export function GeneralTab({ config, update, errors, clearFieldError, assistantName, setAssistantName }: SettingsTabProps & { assistantName: string; setAssistantName: (name: string) => void }) {
  return <section className="panel stack" aria-labelledby="general-heading"><h2 id="general-heading">General</h2>
    <div className="form-grid">
      <Field path="assistant_name" label="Assistant name">{(fieldId) => <input id={fieldId} value={assistantName} onChange={(event) => setAssistantName(event.target.value)} />}</Field>
      <Field path="business_name" label="Business name" error={errors.business_name}>{(fieldId, describedBy) => <input id={fieldId} value={config.business_name} {...invalidProps(errors, "business_name")} aria-describedby={describedBy} onChange={(event) => { clearFieldError("business_name"); update((draft) => { draft.business_name = event.target.value; }); }} />}</Field>
      <VoicePicker
        language={config.language}
        voiceId={config.voice_id ?? null}
        onChange={({ language, voiceId }) => update((draft) => {
          draft.language = language;
          draft.voice_id = voiceId;
        })}
      />
    </div>
    <Field path="greeting" label="Greeting" error={errors.greeting} help="The first thing the assistant says.">{(fieldId, describedBy) => <textarea id={fieldId} value={config.greeting} {...invalidProps(errors, "greeting")} aria-describedby={describedBy} onChange={(event) => { clearFieldError("greeting"); update((draft) => { draft.greeting = event.target.value; }); }} />}</Field>
    <Field path="instructions" label="Instructions" help="Your own prompt: tone, what to ask, and what to avoid.">{(fieldId, describedBy) => <textarea id={fieldId} value={config.instructions} aria-describedby={describedBy} onChange={(event) => update((draft) => { draft.instructions = event.target.value; })} />}</Field>
  </section>;
}
