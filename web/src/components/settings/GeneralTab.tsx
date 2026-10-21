import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api, unwrap, type Schemas } from "../../api/client";
import { errorMessage } from "../../api/errors";
import type { AssistantConfig } from "../../lib/config";
import { Field, type SettingsTabProps } from "./fields";
import { invalidProps } from "./errors";
import { VoicePicker } from "../VoicePicker";
import { ServicesEditor } from "./ServicesEditor";

export function GeneralTab(props: SettingsTabProps & { assistantName: string; setAssistantName: (name: string) => void }) {
  const { assistantId, config, saved, update, errors, clearFieldError, assistantName, setAssistantName } = props;
  const [servicesOpen, setServicesOpen] = useState(Object.keys(errors).some((path) => path.startsWith("services.")));
  const [suggestNote, setSuggestNote] = useState("");
  const [linesLanguage, setLinesLanguage] = useState(saved.language);
  const serviceCount = Object.keys(config.services).length;
  const fieldCount = Object.values(config.services).reduce((total, service) => total + service.fields.length, 0);
  const suggest = useMutation({
    mutationFn: () => unwrap(api.POST("/assistants/{assistant_id}/services-draft", { params: { path: { assistant_id: assistantId } } })),
    onSuccess: (result) => {
      const services = result.services as AssistantConfig["services"];
      if (Object.keys(services).length) update((draft) => {
        draft.services = services;
        draft.urgency_rules = draft.urgency_rules.filter((rule) => rule.type !== "date_deadline" || rule.service in services);
      });
      setServicesOpen(true);
      setSuggestNote(result.warnings.length ? result.warnings.join(" ") : "Suggested from your prompt and knowledge. Review, then save.");
    },
    onError: (error) => setSuggestNote(errorMessage(error, "Suggestions are unavailable right now.")),
  });
  const translateLines = useMutation({
    mutationFn: (body: Schemas["TranslateLinesRequest"]) => unwrap(
      api.POST("/assistants/{assistant_id}/translate-lines", {
        params: { path: { assistant_id: assistantId } },
        body,
      }),
    ),
    onSuccess: (translated, request) => {
      setLinesLanguage(request.locale);
      update((draft) => {
        if (translated.greeting != null && draft.greeting === request.lines.greeting) {
          draft.greeting = translated.greeting;
        }
        if (draft.call_settings) {
          for (const key of [
            "max_call_message",
            "still_there_prompt",
            "silence_goodbye_message",
          ] as const) {
            const value = translated.call_settings?.[key];
            const original = request.lines.call_settings?.[key];
            if (value != null && original != null && draft.call_settings[key] === original) {
              draft.call_settings[key] = value;
            }
          }
        }
        if (
          translated.transfer_handoff_message != null &&
          draft.transfer_handoff_message === request.lines.transfer_handoff_message
        ) {
          draft.transfer_handoff_message = translated.transfer_handoff_message;
        }
        if (
          translated.transfer_failed_message != null &&
          draft.transfer_failed_message === request.lines.transfer_failed_message
        ) {
          draft.transfer_failed_message = translated.transfer_failed_message;
        }
      });
    },
  });
  const showTranslationNotice = config.language !== linesLanguage && config.language !== "multi";
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
        languageNotice={(languageLabel) => showTranslationNotice && (
          <div className="language-translation-notice">
            <p className="help" role="note">
              Your greeting and call messages may still be in another language.{" "}
              <button
                type="button"
                className="button-link"
                disabled={translateLines.isPending}
                onClick={() => translateLines.mutate({
                  locale: config.language,
                  lines: {
                    greeting: config.greeting,
                    call_settings: {
                      max_call_message: config.call_settings?.max_call_message ?? "",
                      still_there_prompt: config.call_settings?.still_there_prompt ?? "",
                      silence_goodbye_message: config.call_settings?.silence_goodbye_message ?? "",
                    },
                    transfer_handoff_message: config.transfer_handoff_message,
                    transfer_failed_message: config.transfer_failed_message,
                  },
                })}
              >
                {translateLines.isPending ? "Translating…" : `Translate them to ${languageLabel}`}
              </button>
            </p>
            {translateLines.isPending && <p className="help" role="status">Translating greeting and call messages…</p>}
            {translateLines.error && <p role="alert">{errorMessage(translateLines.error, "Could not translate the greeting and call messages.")}</p>}
          </div>
        )}
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
