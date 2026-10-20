import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api, unwrap } from "../../api/client";
import { errorMessage } from "../../api/errors";
import type { AssistantConfig } from "../../lib/config";
import { Field, type SettingsTabProps } from "./fields";
import { invalidProps } from "./errors";
import { VoicePicker } from "../VoicePicker";
import { ServicesEditor } from "./ServicesEditor";

export function GeneralTab(props: SettingsTabProps & { assistantName: string; setAssistantName: (name: string) => void }) {
  const { assistantId, config, update, errors, clearFieldError, assistantName, setAssistantName } = props;
  const [servicesOpen, setServicesOpen] = useState(Object.keys(errors).some((path) => path.startsWith("services.")));
  const [suggestNote, setSuggestNote] = useState("");
  const serviceCount = Object.keys(config.services).length;
  const fieldCount = Object.values(config.services).reduce((total, service) => total + service.fields.length, 0);
  const suggest = useMutation({
    mutationFn: () => unwrap(api.POST("/assistants/{assistant_id}/services-draft", { params: { path: { assistant_id: assistantId } } })),
    onSuccess: (result) => {
      const services = result.services as AssistantConfig["services"];
      if (Object.keys(services).length) update((draft) => { draft.services = services; });
      setServicesOpen(true);
      setSuggestNote(result.warnings.length ? result.warnings.join(" ") : "Suggested from your prompt and knowledge. Review, then save.");
    },
    onError: (error) => setSuggestNote(errorMessage(error, "Suggestions are unavailable right now.")),
  });
  const showServices = servicesOpen || Object.keys(errors).some((path) => path.startsWith("services."));
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
  <section className="panel stack services-card" aria-labelledby="services-heading">
    <div className="services-card-header">
      <div>
        <h2 id="services-heading">Services and intake fields</h2>
        <p className="muted">{serviceCount} service{serviceCount === 1 ? "" : "s"} · {fieldCount} intake field{fieldCount === 1 ? "" : "s"}. Derived from your prompt; the assistant collects these details and reads back the critical ones.</p>
      </div>
      <div className="button-row">
        <button type="button" className="button-secondary" onClick={() => suggest.mutate()} disabled={suggest.isPending}>{suggest.isPending ? "Suggesting…" : "Suggest from prompt"}</button>
        <button type="button" aria-expanded={showServices} aria-controls="services-editor" onClick={() => setServicesOpen((open) => !open)}>{showServices ? "Hide services and intake fields" : "Edit services and intake fields"}</button>
      </div>
    </div>
    {suggestNote && <p className="help" role="status">{suggestNote}</p>}
    {showServices && <div id="services-editor" className="stack"><ServicesEditor {...props} /></div>}
  </section></>;
}
