import { Field, type SettingsTabProps } from "./fields";
import { invalidProps } from "./errors";
import { VoicePicker } from "../VoicePicker";
import { ServicesEditor } from "./ServicesEditor";

export function GeneralTab(props: SettingsTabProps & { assistantName: string; setAssistantName: (name: string) => void }) {
  const { config, update, errors, clearFieldError, assistantName, setAssistantName } = props;
  return <>
  <section className="panel stack" aria-labelledby="basics-heading"><h2 id="basics-heading">Basics</h2>
      <Field path="assistant_name" label="Assistant name" layout="row">{(fieldId) => <input id={fieldId} value={assistantName} onChange={(event) => setAssistantName(event.target.value)} />}</Field>
      <Field path="business_name" label="Business name" layout="row" error={errors.business_name}>{(fieldId, describedBy) => <input id={fieldId} value={config.business_name} {...invalidProps(errors, "business_name")} aria-describedby={describedBy} onChange={(event) => { clearFieldError("business_name"); update((draft) => { draft.business_name = event.target.value; }); }} />}</Field>
      <VoicePicker
        language={config.language}
        voiceId={config.voice_id ?? null}
        onChange={({ language, voiceId }) => update((draft) => {
          draft.language = language;
          draft.voice_id = voiceId;
        })}
      />
  </section>
  <section className="panel stack" aria-labelledby="greeting-heading"><h2 id="greeting-heading">Greeting</h2>
    <Field path="greeting" label="Greeting" error={errors.greeting} help="The first thing the assistant says.">{(fieldId, describedBy) => <textarea id={fieldId} value={config.greeting} {...invalidProps(errors, "greeting")} aria-describedby={describedBy} onChange={(event) => { clearFieldError("greeting"); update((draft) => { draft.greeting = event.target.value; }); }} />}</Field>
  </section>
  <section className="panel stack" aria-labelledby="instructions-heading"><h2 id="instructions-heading">Instructions</h2>
    <Field path="instructions" label="Custom prompt" help="Your own prompt: tone, what to ask, what to avoid, and anything else about the business.">{(fieldId, describedBy) => <textarea id={fieldId} value={config.instructions} aria-describedby={describedBy} onChange={(event) => update((draft) => { draft.instructions = event.target.value; })} />}</Field>
  </section>
  <section className="panel stack" aria-labelledby="services-heading"><h2 id="services-heading">Services and intake fields</h2>
    <p className="help">What the assistant can help with, and the details it collects for each. Field names are used to extract and confirm caller details, and critical fields are read back.</p>
    <ServicesEditor {...props} />
  </section></>;
}
