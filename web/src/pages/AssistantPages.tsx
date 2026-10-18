import { useEffect, useMemo, useState, type ChangeEvent, type FormEvent, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, unwrap, uploadKnowledgeFile, type Schemas } from "../api/client";
import { ApiError, fieldErrorsFrom422 } from "../api/errors";
import { TestCallButton } from "../components/TestCallButton";
import { PostActionsEditor } from "../components/PostActionsEditor";
import type { AssistantConfig } from "../lib/config";
import { validateAssistantConfig } from "../lib/config";
import { enumAt, rangeAt } from "../lib/configSchema";
import { diffConfigs } from "../lib/diff";
import { Badge, EmptyState, ErrorState, ErrorSummary, FieldError, LoadingState } from "../components/States";

type FieldSpec = Schemas["FieldSpec"];
type BookingConfig = NonNullable<AssistantConfig["booking"]>;

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

function Field({ path, label, error, help, children }: { path: string; label: string; error?: string; help?: string; children: (id: string, describedBy?: string) => ReactNode }) {
  const id = `field-${path}`;
  const errorId = `${id}-error`;
  return <div className="field">
    <label htmlFor={id}>{label}</label>
    {children(id, error ? `${errorId}${help ? ` ${id}-help` : ""}` : help ? `${id}-help` : undefined)}
    {help && <span id={`${id}-help`} className="help">{help}</span>}
    <FieldError id={errorId}>{error}</FieldError>
  </div>;
}

function isHttpUrl(value: string) {
  try { const parsed = new URL(value); return parsed.protocol === "http:" || parsed.protocol === "https:"; } catch { return false; }
}

function prepareConfigForSave(config: AssistantConfig): AssistantConfig {
  const result = structuredClone(config);
  Object.values(result.services).forEach((service) => service.fields.forEach((field) => {
    if (field.choices) field.choices = field.choices.map((choice) => choice.trim()).filter(Boolean);
  }));
  result.urgency_rules.forEach((rule) => {
    if (rule.type === "keyword") rule.phrases = rule.phrases.map((phrase) => phrase.trim()).filter(Boolean);
  });
  return result;
}

export function AssistantsPage() {
  const navigate = useNavigate();
  const [selectedTemplate, setSelectedTemplate] = useState("");
  const [assistantName, setAssistantName] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [error, setError] = useState("");
  const assistants = useQuery({ queryKey: ["assistants"], queryFn: () => unwrap(api.GET("/assistants")) });
  const templatesQuery = useQuery({ queryKey: ["templates"], queryFn: () => unwrap(api.GET("/templates")), enabled: showCreate });
  const create = useMutation({
    mutationFn: (input: Schemas["AssistantCreate"]) => unwrap(api.POST("/assistants", { body: input })),
    onSuccess: (assistant) => navigate(`/assistants/${assistant.id}/settings`),
    onError: (caught) => setError(caught instanceof Error ? caught.message : "Unable to create assistant."),
  });
  const selected = templatesQuery.data?.find(({ name }) => name === selectedTemplate);
  return <main className="page" id="main-content">
    <p className="eyebrow">Configuration</p><h1>Assistants</h1>
    <div className="button-row"><button className="button-primary" type="button" onClick={() => setShowCreate((value) => !value)}>Create from template</button>
      <Link className="button-primary" to="/assistants/new">Set up from website</Link>
    </div>
    {showCreate && <section className="panel" aria-labelledby="create-assistant-heading">
      <h2 id="create-assistant-heading">Create an assistant</h2>
      {templatesQuery.isLoading && <LoadingState />}
      {templatesQuery.error && <ErrorState message="Templates could not be loaded." />}
      {selected && <p className="muted"><strong>{selected.config.business_name}</strong> · {Object.values(selected.config.services).map((service) => service.label).join(", ") || "No services yet"}</p>}
      <form className="stack" onSubmit={(event) => {
        event.preventDefault();
        if (!selected) { setError("Choose a template."); return; }
        setError("");
        create.mutate({ name: assistantName, template: selected.name, config: selected.config });
      }}>
        <fieldset className="stack"><legend>Template</legend>
          {(templatesQuery.data ?? []).map((template) => <label key={template.name} className="radio-row">
            <input type="radio" name="template" value={template.name} checked={selectedTemplate === template.name} onChange={() => setSelectedTemplate(template.name)} />
            <span><strong>{template.name.replaceAll("_", " ")}</strong><br /><span className="muted">{template.config.business_name} · {Object.values(template.config.services).map((service) => service.label).join(", ") || "Blank configuration"}</span></span>
          </label>)}
        </fieldset>
        <div className="field"><label htmlFor="new-assistant-name">Assistant name</label><input id="new-assistant-name" required value={assistantName} onChange={(event) => setAssistantName(event.target.value)} /></div>
        {error && <p role="alert">{error}</p>}
        <button className="button-primary" type="submit" disabled={create.isPending || !selected}>Create assistant</button>
      </form>
    </section>}
    {assistants.isLoading && <LoadingState />}
    {assistants.error && <ErrorState message="Assistants could not be loaded." />}
    {!assistants.isLoading && !assistants.error && !assistants.data?.items.length && <EmptyState>No assistants yet.</EmptyState>}
    <div className="card-grid">{assistants.data?.items.map((assistant) => <article className="panel" key={assistant.id}>
      <p className="eyebrow">{assistant.template ?? "Custom"}</p>
      <h2>{assistant.name}</h2><p>{assistant.config.business_name}</p>
      <p className="muted">{Object.values(assistant.config.services).map((service) => service.label).join(" · ")}</p>
      <div className="button-row">
        <Link to={`/assistants/${assistant.id}/settings`}>Manage assistant</Link>
        <TestCallButton assistantId={assistant.id} />
      </div>
    </article>)}</div>
  </main>;
}

export function AssistantSettingsPage() {
  const { id = "" } = useParams();
  const queryClient = useQueryClient();
  const assistantQuery = useQuery({ queryKey: ["assistant", id], queryFn: () => unwrap(api.GET("/assistants/{assistant_id}", { params: { path: { assistant_id: id } } })) });
  const schemaQuery = useQuery({ queryKey: ["assistant-config-schema"], queryFn: () => unwrap(api.GET("/assistant-config/schema")) });
  const versionsQuery = useQuery({ queryKey: ["assistant-versions", id], queryFn: () => unwrap(api.GET("/assistants/{assistant_id}/versions", { params: { path: { assistant_id: id } } })) });
  const [config, setConfig] = useState<AssistantConfig | null>(null);
  const [assistantName, setAssistantName] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [serviceKeyErrors, setServiceKeyErrors] = useState<Record<string, string>>({});
  const [contactKeyErrors, setContactKeyErrors] = useState<Record<string, string>>({});
  const [choiceText, setChoiceText] = useState<Record<string, string>>({});
  const [phraseText, setPhraseText] = useState<Record<string, string>>({});
  const [saveMessage, setSaveMessage] = useState("");
  const [selectedVersion, setSelectedVersion] = useState("");
  useEffect(() => {
    if (!assistantQuery.data) return;
    setConfig(structuredClone(assistantQuery.data.config));
    setAssistantName(assistantQuery.data.name);
    setServiceKeyErrors({});
    setContactKeyErrors({});
    setChoiceText({});
    setPhraseText({});
  }, [assistantQuery.data]);

  const readbackOptions = useMemo(() => enumAt(schemaQuery.data, ["properties", "readback", "enum"], ["off", "email", "critical"]), [schemaQuery.data]);
  const actionOptions = useMemo(() => enumAt(schemaQuery.data, ["properties", "live_actions", "items", "enum"], ["confirm_critical_fields", "flag_urgent", "request_callback", "log_complaint", "check_availability", "book_appointment", "end_call", "transfer_call"]), [schemaQuery.data]);
  const typeOptions = useMemo(() => enumAt(schemaQuery.data, ["$defs", "FieldSpec", "properties", "type", "enum"], ["string", "email", "phone", "date", "boolean", "integer", "number", "enum", "list"]), [schemaQuery.data]);
  const thresholdRange = useMemo(() => rangeAt(schemaQuery.data), [schemaQuery.data]);
  const serverErrors = errors;

  const save = useMutation({
    mutationFn: (payload: Schemas["AssistantUpdate"]) => unwrap(api.PUT("/assistants/{assistant_id}", { params: { path: { assistant_id: id } }, body: payload })),
    onSuccess: async (assistant) => {
      setConfig(structuredClone(assistant.config));
      setAssistantName(assistant.name);
      setErrors({});
      setServiceKeyErrors({});
      setContactKeyErrors({});
      setChoiceText({});
      setPhraseText({});
      setSaveMessage(`Saved version ${assistant.version}`);
      await Promise.all([queryClient.invalidateQueries({ queryKey: ["assistant", id] }), queryClient.invalidateQueries({ queryKey: ["assistant-versions", id] })]);
    },
    onError: (caught) => {
      if (caught instanceof ApiError && caught.status === 422) {
        setErrors(fieldErrorsFrom422(caught.body));
      } else setErrors({ _form: caught instanceof Error ? caught.message : "Unable to save changes." });
    },
  });
  if (assistantQuery.error) return <main className="page" id="main-content"><ErrorState message="Assistant settings could not be loaded." /></main>;
  if (assistantQuery.isLoading || !config) return <main className="page" id="main-content"><LoadingState /></main>;
  const baseline = assistantQuery.data!;
  const clientErrors = validateAssistantConfig(config);
  const allErrors = { ...clientErrors, ...serverErrors, ...serviceKeyErrors, ...contactKeyErrors };
  const isDirty = JSON.stringify(config) !== JSON.stringify(baseline.config) || assistantName !== baseline.name;
  const update = (change: (draft: AssistantConfig) => void) => {
    setConfig((current) => {
      if (!current) return current;
      const draft = structuredClone(current);
      change(draft);
      return draft;
    });
    setSaveMessage("");
  };
  const clearFieldError = (path: string) => {
    setErrors((current) => {
      const next = { ...current };
      delete next[path];
      return next;
    });
    setSaveMessage("");
  };
  const inputProps = (path: string) => ({
    "aria-invalid": Boolean(allErrors[path]),
    "aria-describedby": allErrors[path] ? `field-${path}-error` : undefined,
  });
  const serviceKeys = Object.keys(config.services);
  const bookingConfig = config.booking;

  function saveConfig(event: FormEvent) {
    event.preventDefault();
    if (!config) return;
    const payloadConfig = prepareConfigForSave(config);
    const validation = validateAssistantConfig(payloadConfig);
    setErrors(validation);
    setSaveMessage("");
    if (Object.keys(validation).length || Object.keys(serviceKeyErrors).length || Object.keys(contactKeyErrors).length) {
      document.querySelector<HTMLElement>(".error-summary a")?.focus();
      return;
    }
    save.mutate({ name: assistantName, template: baseline.template, config: payloadConfig });
  }

  const chosen = versionsQuery.data?.items.find(({ id: versionId }) => versionId === selectedVersion);
  const summaryErrors = Object.fromEntries(Object.entries(allErrors).filter(([path]) => path !== "_form"));
  return <main className="page" id="main-content">
    <p className="eyebrow">Assistant settings</p><h1>{baseline.name}</h1>
    <ChannelConnections assistantId={id} bookingEnabled={Boolean(config.booking)} />
    <TestCallButton assistantId={baseline.id} />
    <p className="muted">{isDirty ? "Unsaved changes" : `Version ${baseline.version} is up to date`}</p>
    <form className="stack" onSubmit={saveConfig} noValidate>
      <ErrorSummary errors={summaryErrors} />
      {allErrors._form && <p role="alert">{allErrors._form}</p>}
      <PostActionsEditor assistantId={id} config={config} update={update} />
      <section className="panel stack"><h2>Basics</h2>
        <Field path="assistant_name" label="Assistant name">{(fieldId) => <input id={fieldId} value={assistantName} onChange={(event) => setAssistantName(event.target.value)} />}</Field>
        <Field path="business_name" label="Business name" error={allErrors.business_name}>{(fieldId, describedBy) => <input id={fieldId} value={config.business_name} {...inputProps("business_name")} aria-describedby={describedBy} onChange={(event) => { clearFieldError("business_name"); update((draft) => { draft.business_name = event.target.value; }); }} />}</Field>
        <Field path="language" label="Language">{(fieldId) => <input id={fieldId} value={config.language} onChange={(event) => update((draft) => { draft.language = event.target.value; })} />}</Field>
        <Field path="greeting" label="Greeting" error={allErrors.greeting}>{(fieldId, describedBy) => <textarea id={fieldId} value={config.greeting} {...inputProps("greeting")} aria-describedby={describedBy} onChange={(event) => { clearFieldError("greeting"); update((draft) => { draft.greeting = event.target.value; }); }} />}</Field>
        <Field path="instructions" label="Instructions">{(fieldId) => <textarea id={fieldId} value={config.instructions} onChange={(event) => update((draft) => { draft.instructions = event.target.value; })} />}</Field>
      </section>
      <section className="panel stack"><h2>Email triage</h2>
        <p className="help">How the email agent decides whether to draft a reply, hand the email to a person, or ignore it.</p>
        {([
          ["enquiry", "Enquiry"],
          ["needs_human", "Needs a person"],
          ["spam", "Spam"],
          ["not_relevant", "Not relevant"],
        ] as const).map(([key, label]) => <Field key={key} path={`email_triage.${key}`} label={label}>{(fieldId) => <textarea id={fieldId} value={config.email_triage?.[key] ?? ""} onChange={(event) => update((draft) => { if (draft.email_triage) draft.email_triage[key] = event.target.value; })} />}</Field>)}
      </section>
      <section className="panel stack"><h2>Policies</h2>
        {config.policies.map((policy, index) => <div className="inline-editor" key={`policy-${index}`}>
          <Field path={`policies.${index}`} label={`Policy ${index + 1}`}>{(fieldId) => <textarea id={fieldId} value={policy} onChange={(event) => update((draft) => { draft.policies[index] = event.target.value; })} />}</Field>
          <div className="button-row"><button type="button" onClick={() => update((draft) => { [draft.policies[index - 1], draft.policies[index]] = [draft.policies[index], draft.policies[index - 1]]; })} disabled={index === 0}>Move up</button><button type="button" onClick={() => update((draft) => { [draft.policies[index + 1], draft.policies[index]] = [draft.policies[index], draft.policies[index + 1]]; })} disabled={index === config.policies.length - 1}>Move down</button><button type="button" onClick={() => update((draft) => { draft.policies.splice(index, 1); })}>Remove policy</button></div>
        </div>)}
        <button type="button" onClick={() => update((draft) => { draft.policies.push(""); })}>Add policy</button>
      </section>
      <section className="panel stack"><h2>Readback</h2>
        <p className="help">Choose which information the assistant reads back for caller confirmation.</p>
        {readbackOptions.map((option) => <label key={option} className="radio-row"><input type="radio" name="readback" value={option} checked={config.readback === option} onChange={() => update((draft) => { draft.readback = option as AssistantConfig["readback"]; })} /><span><strong>{option}</strong> — {option === "off" ? "Don't read details back to the caller." : option === "email" ? "Read email addresses back letter by letter so the caller can confirm them." : "Read back every field marked critical (for example name, phone, email or key dates) and ask the caller to confirm."}</span></label>)}
      </section>
      <section className="panel stack"><h2>Services and intake fields</h2>
        {serviceKeys.map((serviceKey) => <section className="inline-editor" key={serviceKey}>
          <div className="form-grid">
            <Field path={`services.${serviceKey}.key`} label="Service key" error={allErrors[`services.${serviceKey}.key`]}>{(fieldId, describedBy) => <input id={fieldId} value={serviceKey} {...inputProps(`services.${serviceKey}.key`)} aria-describedby={describedBy} onChange={(event) => {
              const nextKey = event.target.value;
              if (nextKey !== serviceKey && nextKey in config.services) {
                setServiceKeyErrors((current) => ({ ...current, [`services.${serviceKey}.key`]: "Service keys must be unique." }));
                return;
              }
              setServiceKeyErrors((current) => {
                const next = { ...current };
                delete next[`services.${serviceKey}.key`];
                return next;
              });
              update((draft) => {
              const entries = Object.entries(draft.services);
              const index = entries.findIndex(([key]) => key === serviceKey);
              entries[index] = [nextKey, entries[index][1]];
              draft.services = Object.fromEntries(entries);
              draft.urgency_rules.forEach((rule) => { if (rule.type === "date_deadline" && rule.service === serviceKey) rule.service = nextKey; });
            }); }} />}</Field>
            <Field path={`services.${serviceKey}.label`} label="Service label">{(fieldId) => <input id={fieldId} value={config.services[serviceKey].label} onChange={(event) => update((draft) => { draft.services[serviceKey].label = event.target.value; })} />}</Field>
          </div>
          {config.services[serviceKey].fields.map((field, fieldIndex) => <div className="inline-editor" key={`${serviceKey}-${fieldIndex}`}>
            <div className="form-grid">
              <Field path={`services.${serviceKey}.fields.${fieldIndex}.name`} label="Field name" error={allErrors[`services.${serviceKey}.fields.${fieldIndex}.name`]}>{(fieldId, describedBy) => <input id={fieldId} value={field.name} {...inputProps(`services.${serviceKey}.fields.${fieldIndex}.name`)} aria-describedby={describedBy} onChange={(event) => {
                const nextName = event.target.value;
                update((draft) => {
                  const fieldToRename = draft.services[serviceKey].fields[fieldIndex];
                  const previousName = fieldToRename.name;
                  fieldToRename.name = nextName;
                  draft.urgency_rules.forEach((rule) => {
                    if (rule.type === "date_deadline" && rule.service === serviceKey && rule.field === previousName) rule.field = nextName;
                  });
                });
              }} />}</Field>
              <Field path={`services.${serviceKey}.fields.${fieldIndex}.label`} label="Field label" error={allErrors[`services.${serviceKey}.fields.${fieldIndex}.label`]}>{(fieldId, describedBy) => <input id={fieldId} value={field.label} {...inputProps(`services.${serviceKey}.fields.${fieldIndex}.label`)} aria-describedby={describedBy} onChange={(event) => update((draft) => { draft.services[serviceKey].fields[fieldIndex].label = event.target.value; })} />}</Field>
              <Field path={`services.${serviceKey}.fields.${fieldIndex}.type`} label="Field type" error={allErrors[`services.${serviceKey}.fields.${fieldIndex}.type`]}>{(fieldId, describedBy) => <select id={fieldId} value={field.type} {...inputProps(`services.${serviceKey}.fields.${fieldIndex}.type`)} aria-describedby={describedBy} onChange={(event) => {
                const path = `services.${serviceKey}.fields.${fieldIndex}.choices`;
                setChoiceText((current) => { const next = { ...current }; delete next[path]; return next; });
                update((draft) => {
                  const item = draft.services[serviceKey].fields[fieldIndex];
                  item.type = event.target.value as FieldSpec["type"];
                  if (item.type !== "enum") delete item.choices;
                  else item.choices = [];
                });
              }}>{typeOptions.map((type) => <option key={type} value={type}>{type}</option>)}</select>}</Field>
              {field.type === "enum" && <Field path={`services.${serviceKey}.fields.${fieldIndex}.choices`} label="Choices" error={allErrors[`services.${serviceKey}.fields.${fieldIndex}.choices`]}>{(fieldId, describedBy) => {
                const path = `services.${serviceKey}.fields.${fieldIndex}.choices`;
                return <input id={fieldId} value={choiceText[path] ?? field.choices?.join(", ") ?? ""} {...inputProps(path)} aria-describedby={describedBy} onChange={(event) => {
                  const value = event.target.value;
                  setChoiceText((current) => ({ ...current, [path]: value }));
                  update((draft) => { draft.services[serviceKey].fields[fieldIndex].choices = value.split(",").map((choice) => choice.trim()); });
                }} />;
              }}</Field>}
              <Field path={`services.${serviceKey}.fields.${fieldIndex}.description`} label="Description">{(fieldId) => <input id={fieldId} value={field.description} onChange={(event) => update((draft) => { draft.services[serviceKey].fields[fieldIndex].description = event.target.value; })} />}</Field>
            </div>
            <div className="button-row">
              <label className="check-row"><input type="checkbox" checked={field.required} onChange={(event) => update((draft) => { draft.services[serviceKey].fields[fieldIndex].required = event.target.checked; })} />Required</label>
              <label className="check-row"><input type="checkbox" checked={field.critical} onChange={(event) => update((draft) => { draft.services[serviceKey].fields[fieldIndex].critical = event.target.checked; })} />Critical</label>
              <button type="button" onClick={() => update((draft) => { draft.services[serviceKey].fields.splice(fieldIndex, 1); })}>Remove field</button>
            </div>
          </div>)}
          <div className="button-row"><button type="button" onClick={() => update((draft) => { draft.services[serviceKey].fields.push({ name: "", label: "", type: "string", required: false, critical: false, description: "" }); })}>Add field</button><button type="button" onClick={() => update((draft) => {
            delete draft.services[serviceKey];
            draft.urgency_rules = draft.urgency_rules.filter((rule) => rule.type !== "date_deadline" || rule.service !== serviceKey);
          })}>Remove service</button></div>
        </section>)}
        <button type="button" onClick={() => update((draft) => { let key = "service"; let suffix = 1; while (draft.services[key]) key = `service_${suffix++}`; draft.services[key] = { label: "", fields: [] }; })}>Add service</button>
      </section>
      <section className="panel stack"><h2>Urgency rules</h2>
        {config.urgency_rules.map((rule, index) => <section className="inline-editor" key={`urgency-${index}`}>
          <Field path={`urgency_rules.${index}.type`} label="Rule type">{(fieldId) => <select id={fieldId} value={rule.type} onChange={(event) => {
            setPhraseText((current) => { const next = { ...current }; delete next[`urgency_rules.${index}.phrases`]; return next; });
            update((draft) => {
              draft.urgency_rules[index] = event.target.value === "keyword" ? { type: "keyword", phrases: [], reason: "" } : { type: "date_deadline", service: serviceKeys[0] ?? "", field: "", add_months: 0, minus_days: 0, urgent_within_days: 0, reason: "" };
            });
          }}><option value="keyword">Keyword</option><option value="date_deadline">Date deadline</option></select>}</Field>
          {rule.type === "keyword" ? <>
            <Field path={`urgency_rules.${index}.phrases`} label="Phrases (comma or newline separated)" error={allErrors[`urgency_rules.${index}.phrases`]}>{(fieldId, describedBy) => {
              const path = `urgency_rules.${index}.phrases`;
              return <textarea id={fieldId} value={phraseText[path] ?? rule.phrases.join(", ")} {...inputProps(path)} aria-describedby={describedBy} onChange={(event) => {
                const value = event.target.value;
                setPhraseText((current) => ({ ...current, [path]: value }));
                update((draft) => {
                  const current = draft.urgency_rules[index];
                  if (current?.type === "keyword") current.phrases = value.split(/,|\n/).map((item) => item.trim());
                });
              }} />;
            }}</Field>
            <Field path={`urgency_rules.${index}.reason`} label="Reason" error={allErrors[`urgency_rules.${index}.reason`]}>{(fieldId, describedBy) => <input id={fieldId} value={rule.reason} {...inputProps(`urgency_rules.${index}.reason`)} aria-describedby={describedBy} onChange={(event) => update((draft) => { const current = draft.urgency_rules[index]; if (current?.type === "keyword") current.reason = event.target.value; })} />}</Field>
          </> : <>
            <div className="form-grid">
              <Field path={`urgency_rules.${index}.service`} label="Service" error={allErrors[`urgency_rules.${index}.service`]}>{(fieldId, describedBy) => <select id={fieldId} value={rule.service} {...inputProps(`urgency_rules.${index}.service`)} aria-describedby={describedBy} onChange={(event) => update((draft) => { const current = draft.urgency_rules[index]; if (current?.type === "date_deadline") { current.service = event.target.value; current.field = ""; } })}>{serviceKeys.map((key) => <option key={key} value={key}>{config.services[key].label || key}</option>)}</select>}</Field>
              <Field path={`urgency_rules.${index}.field`} label="Date field" error={allErrors[`urgency_rules.${index}.field`]}>{(fieldId, describedBy) => <select id={fieldId} value={rule.field} {...inputProps(`urgency_rules.${index}.field`)} aria-describedby={describedBy} onChange={(event) => update((draft) => { const current = draft.urgency_rules[index]; if (current?.type === "date_deadline") current.field = event.target.value; })}><option value="">Choose a date field</option>{config.services[rule.service]?.fields.filter(({ type }) => type === "date").map(({ name, label }) => <option key={name} value={name}>{label}</option>)}</select>}</Field>
            </div>
            {(["add_months", "minus_days", "urgent_within_days"] as const).map((key) => <Field key={key} path={`urgency_rules.${index}.${key}`} label={key.replaceAll("_", " ")} error={allErrors[`urgency_rules.${index}.${key}`]}>{(fieldId, describedBy) => <input id={fieldId} type="number" min="0" step="1" value={rule[key]} {...inputProps(`urgency_rules.${index}.${key}`)} aria-describedby={describedBy} onChange={(event) => update((draft) => { const current = draft.urgency_rules[index]; if (current?.type === "date_deadline") current[key] = Number(event.target.value); })} />}</Field>)}
            <Field path={`urgency_rules.${index}.reason`} label="Reason" error={allErrors[`urgency_rules.${index}.reason`]}>{(fieldId, describedBy) => <input id={fieldId} value={rule.reason} {...inputProps(`urgency_rules.${index}.reason`)} aria-describedby={describedBy} onChange={(event) => update((draft) => { const current = draft.urgency_rules[index]; if (current?.type === "date_deadline") current.reason = event.target.value; })} />}</Field>
          </>}
          <button type="button" onClick={() => { setPhraseText({}); update((draft) => { draft.urgency_rules.splice(index, 1); }); }}>Remove urgency rule</button>
        </section>)}
        <button type="button" onClick={() => update((draft) => { draft.urgency_rules.push({ type: "keyword", phrases: [], reason: "" }); })}>Add urgency rule</button>
      </section>
      <section className="panel stack"><h2>Live actions</h2><p className="help">Live actions add latency and default to none.</p>
        {actionOptions.map((action) => <label className="check-row" key={action}><input type="checkbox" checked={config.live_actions.includes(action as AssistantConfig["live_actions"][number])} onChange={(event) => update((draft) => { const value = action as AssistantConfig["live_actions"][number]; draft.live_actions = event.target.checked ? [...draft.live_actions, value] : draft.live_actions.filter((item) => item !== value); })} /><span><strong>{action.replaceAll("_", " ")}</strong> — {liveActionHelp[action] ?? "Available during a live interaction."}</span></label>)}
      </section>
      <section className="panel stack"><h2>Review threshold</h2>
        <Field path="review_confidence_threshold" label={`Confidence threshold: ${config.review_confidence_threshold.toFixed(2)}`} error={allErrors.review_confidence_threshold} help="Fields extracted below this confidence go to the review queue">{(fieldId, describedBy) => <input id={fieldId} type="range" min={thresholdRange.min} max={thresholdRange.max} step="0.05" value={config.review_confidence_threshold} {...inputProps("review_confidence_threshold")} aria-describedby={describedBy} onChange={(event) => update((draft) => { draft.review_confidence_threshold = Number(event.target.value); })} />}</Field>
      </section>
      <section className="panel stack"><h2>Office hours</h2>
        <fieldset><legend>Open days</legend><div className="button-row">{["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].map((day, index) => <label key={day} className="check-row"><input id={index === 0 ? "field-office_hours.days" : undefined} type="checkbox" checked={config.office_hours.days.includes(day)} aria-invalid={Boolean(allErrors["office_hours.days"])} aria-describedby={allErrors["office_hours.days"] ? "field-office_hours.days-error" : undefined} onChange={(event) => update((draft) => { draft.office_hours.days = event.target.checked ? [...draft.office_hours.days, day] : draft.office_hours.days.filter((item) => item !== day); })} />{day.slice(0, 3)}</label>)}</div>{allErrors["office_hours.days"] && <p id="field-office_hours.days-error" className="field-error">{allErrors["office_hours.days"]}</p>}</fieldset>
        <div className="form-grid">
          {(["opens", "closes"] as const).map((key) => <Field key={key} path={`office_hours.${key}`} label={key === "opens" ? "Opens" : "Closes"} error={allErrors[`office_hours.${key}`]}>{(fieldId, describedBy) => <input id={fieldId} type="time" value={config.office_hours[key]} {...inputProps(`office_hours.${key}`)} aria-describedby={describedBy} onChange={(event) => update((draft) => { draft.office_hours[key] = event.target.value; })} />}</Field>)}
          <Field path="office_hours.timezone" label="Timezone">{(fieldId) => <input id={fieldId} value={config.office_hours.timezone} onChange={(event) => update((draft) => { draft.office_hours.timezone = event.target.value; })} />}</Field>
        </div>
      </section>
      <section className="panel stack"><h2>Calendar booking</h2>
        <label className="check-row">
          <input
            type="checkbox"
            checked={Boolean(bookingConfig)}
            onChange={(event) => update((draft) => {
              draft.booking = event.target.checked ? structuredClone(DEFAULT_BOOKING_CONFIG) : null;
            })}
          />
          <span><strong>Enable booking configuration</strong> — bookings remain unavailable until Calendar is connected and both booking live actions are enabled.</span>
        </label>
        {bookingConfig && <>
          <div className="form-grid">
            <Field path="booking.calendar_id" label="Calendar ID" help="Use primary for the connected Google account's primary calendar.">{(fieldId) => <input id={fieldId} value={bookingConfig.calendar_id} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.calendar_id = event.target.value; })} />}</Field>
            <Field path="booking.check_message" label="Availability holding message">{(fieldId) => <input id={fieldId} value={bookingConfig.check_message} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.check_message = event.target.value; })} />}</Field>
            <Field path="booking.book_message" label="Booking holding message">{(fieldId) => <input id={fieldId} value={bookingConfig.book_message} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.book_message = event.target.value; })} />}</Field>
            <Field path="booking.min_notice_hours" label="Minimum notice (hours)">{(fieldId) => <input id={fieldId} type="number" min="0" value={bookingConfig.min_notice_hours} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.min_notice_hours = Number(event.target.value); })} />}</Field>
            <Field path="booking.horizon_days" label="Booking horizon (days)">{(fieldId) => <input id={fieldId} type="number" min="1" max="60" value={bookingConfig.horizon_days} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.horizon_days = Number(event.target.value); })} />}</Field>
            <Field path="booking.buffer_minutes" label="Buffer between appointments (minutes)">{(fieldId) => <input id={fieldId} type="number" min="0" value={bookingConfig.buffer_minutes} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.buffer_minutes = Number(event.target.value); })} />}</Field>
            <Field path="booking.slot_step_minutes" label="Slot step (minutes)">{(fieldId) => <input id={fieldId} type="number" min="5" max="240" value={bookingConfig.slot_step_minutes} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.slot_step_minutes = Number(event.target.value); })} />}</Field>
            <Field path="booking.max_slots_offered" label="Maximum slots offered">{(fieldId) => <input id={fieldId} type="number" min="1" max="5" value={bookingConfig.max_slots_offered} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.max_slots_offered = Number(event.target.value); })} />}</Field>
            <Field path="booking.send_updates" label="Calendar attendee updates">{(fieldId) => <select id={fieldId} value={bookingConfig.send_updates} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.send_updates = event.target.value as BookingConfig["send_updates"]; })}>
              <option value="all">All attendees</option><option value="externalOnly">External attendees</option><option value="none">No updates</option>
            </select>}</Field>
          </div>
          <h3>Appointment types</h3>
          {bookingConfig.appointment_types.map((appointment, index) => <div className="inline-editor stack" key={`${appointment.key}-${index}`}>
            <div className="form-grid">
              <Field path={`booking.appointment_types.${index}.key`} label="Key" error={allErrors[`booking.appointment_types.${index}.key`]}>{(fieldId) => <input id={fieldId} value={appointment.key} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.appointment_types[index].key = event.target.value; })} />}</Field>
              <Field path={`booking.appointment_types.${index}.label`} label="Appointment label">{(fieldId) => <input id={fieldId} value={appointment.label} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.appointment_types[index].label = event.target.value; })} />}</Field>
              <Field path={`booking.appointment_types.${index}.duration_minutes`} label="Duration (minutes)">{(fieldId) => <input id={fieldId} type="number" min="1" max="480" value={appointment.duration_minutes} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.appointment_types[index].duration_minutes = Number(event.target.value); })} />}</Field>
              <Field path={`booking.appointment_types.${index}.location_or_link`} label="Location or meeting link">{(fieldId) => <input id={fieldId} value={appointment.location_or_link ?? ""} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.appointment_types[index].location_or_link = event.target.value || null; })} />}</Field>
              <Field path={`booking.appointment_types.${index}.when_to_use`} label="When to use this appointment type">{(fieldId) => <textarea id={fieldId} value={appointment.when_to_use} onChange={(event) => update((draft) => { if (draft.booking) draft.booking.appointment_types[index].when_to_use = event.target.value; })} />}</Field>
            </div>
            <button type="button" onClick={() => update((draft) => { if (draft.booking) draft.booking.appointment_types.splice(index, 1); })}>Remove appointment type</button>
          </div>)}
          <button type="button" onClick={() => update((draft) => { if (draft.booking) draft.booking.appointment_types.push({ key: "appointment", label: "Appointment", duration_minutes: 30, when_to_use: "", location_or_link: null }); })}>Add appointment type</button>
        </>}
      </section>
      <section className="panel stack"><h2>Price list</h2>
        {config.price_list.map((item, index) => <div className="inline-editor" key={`price-${index}`}>
          <div className="form-grid">{(["service", "price", "notes"] as const).map((key) => <Field key={key} path={`price_list.${index}.${key}`} label={key.replaceAll("_", " ")}>{(fieldId) => key === "notes" ? <textarea id={fieldId} value={item[key]} onChange={(event) => update((draft) => { draft.price_list[index][key] = event.target.value; })} /> : <input id={fieldId} value={item[key]} onChange={(event) => update((draft) => { draft.price_list[index][key] = event.target.value; })} />}</Field>)}</div>
          <button type="button" onClick={() => update((draft) => { draft.price_list.splice(index, 1); })}>Remove price</button>
        </div>)}
        <button type="button" onClick={() => update((draft) => { draft.price_list.push({ service: "", price: "", notes: "" }); })}>Add price</button>
      </section>
      <section className="panel stack"><h2>Transfer targets</h2>
        {(config.transfer_targets ?? []).map((target, index) => <div className="inline-editor" key={`transfer-${index}`}>
          <div className="form-grid">
            {(["key", "label", "phone", "when_to_use"] as const).map((key) => <Field key={key} path={`transfer_targets.${index}.${key}`} label={key === "when_to_use" ? "When to use" : key.replaceAll("_", " ")} error={allErrors[`transfer_targets.${index}.${key}`]}>{(fieldId, describedBy) => key === "when_to_use" ? <textarea id={fieldId} value={target[key]} aria-describedby={describedBy} onChange={(event) => update((draft) => { draft.transfer_targets![index][key] = event.target.value; })} /> : <input id={fieldId} type={key === "phone" ? "tel" : "text"} value={target[key]} aria-describedby={describedBy} onChange={(event) => update((draft) => { draft.transfer_targets![index][key] = event.target.value; })} />}</Field>)}
            <Field path={`transfer_targets.${index}.office_hours_only`} label="Only during office hours">{(fieldId) => <input id={fieldId} type="checkbox" checked={target.office_hours_only} onChange={(event) => update((draft) => { draft.transfer_targets![index].office_hours_only = event.target.checked; })} />}</Field>
          </div>
          <button type="button" onClick={() => update((draft) => { draft.transfer_targets!.splice(index, 1); })}>Remove transfer target</button>
        </div>)}
        <button type="button" onClick={() => update((draft) => { draft.transfer_targets ??= []; draft.transfer_targets.push({ key: "", label: "", phone: "", when_to_use: "", office_hours_only: true }); })}>Add transfer target</button>
        <Field path="transfer_failed_message" label="Message when nobody answers">{(fieldId) => <textarea id={fieldId} value={config.transfer_failed_message} onChange={(event) => update((draft) => { draft.transfer_failed_message = event.target.value; })} />}</Field>
        <Field path="transfer_handoff_message" label="Handoff message">{(fieldId) => <textarea id={fieldId} value={config.transfer_handoff_message} onChange={(event) => update((draft) => { draft.transfer_handoff_message = event.target.value; })} />}</Field>
      </section>
      <section className="panel stack"><h2>Contacts</h2>
        {Object.entries(config.contacts).map(([key, value], index) => <div className="form-grid" key={`${key}-${index}`}>
          <Field path={`contacts.${key}.key`} label="Contact key" error={allErrors[`contacts.${key}.key`]}>{(fieldId, describedBy) => <input id={fieldId} value={key} {...inputProps(`contacts.${key}.key`)} aria-describedby={describedBy} onChange={(event) => {
            const nextKey = event.target.value;
            const path = `contacts.${key}.key`;
            if (nextKey !== key && nextKey in config.contacts) {
              setContactKeyErrors((current) => ({ ...current, [path]: "Contact keys must be unique." }));
              return;
            }
            setContactKeyErrors((current) => { const next = { ...current }; delete next[path]; return next; });
            update((draft) => {
              const values = Object.entries(draft.contacts);
              values[index] = [nextKey, values[index][1]];
              draft.contacts = Object.fromEntries(values);
            });
          }} />}</Field>
          <Field path={`contacts.${key}.value`} label="Contact value">{(fieldId) => <input id={fieldId} value={value} onChange={(event) => update((draft) => { draft.contacts[key] = event.target.value; })} />}</Field>
          <button type="button" onClick={() => update((draft) => { delete draft.contacts[key]; })}>Remove contact</button>
        </div>)}
        <button type="button" onClick={() => update((draft) => { let key = "contact"; let suffix = 1; while (draft.contacts[key]) key = `contact_${suffix++}`; draft.contacts[key] = ""; })}>Add contact</button>
      </section>
      <div className="button-row">
        <button type="submit" className="button-primary" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save changes"}</button>
        <span aria-live="polite" role="status">{saveMessage}</span>
      </div>
    </form>
    <section className="panel stack"><h2>Version history</h2>
      {versionsQuery.isLoading && <LoadingState />}
      <ul>{versionsQuery.data?.items.map((version) => <li key={version.id}><button type="button" onClick={() => setSelectedVersion(version.id)}>Version {version.version} — {formatDate(version.created_at)}</button></li>)}</ul>
      {chosen && <VersionDiff oldConfig={chosen.config} currentConfig={baseline.config} />}
    </section>
  </main>;
}

function VersionDiff({ oldConfig, currentConfig }: { oldConfig: unknown; currentConfig: unknown }) {
  const diff = diffConfigs(oldConfig, currentConfig);
  return <div className="table-wrap"><table><caption>Selected version compared with the current saved configuration</caption><thead><tr><th>Path</th><th>Change</th><th>Old value</th><th>Current value</th></tr></thead><tbody>
    {diff.length ? diff.map((change) => <tr key={change.path}><td><code>{change.path}</code></td><td>{change.kind}</td><td>{change.oldValue ?? "—"}</td><td>{change.newValue ?? "—"}</td></tr>) : <tr><td colSpan={4}>No differences.</td></tr>}
  </tbody></table></div>;
}

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

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

export function ChannelConnections({ assistantId, bookingEnabled = false }: { assistantId: string; bookingEnabled?: boolean }) {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<Schemas["ChannelConnectionCreate"]["kind"]>("phone");
  const [externalId, setExternalId] = useState("");
  const [error, setError] = useState("");
  const gmailMode = useQuery({
    queryKey: ["gmail-mode"],
    queryFn: () => unwrap(api.GET("/channels/gmail/mode")),
  });
  const calendarMode = useQuery({
    queryKey: ["calendar-mode"],
    queryFn: () => unwrap(api.GET("/calendar/mode")),
  });
  const startGmailOAuth = useMutation({
    mutationFn: () => unwrap(api.POST("/assistants/{assistant_id}/channels/gmail/oauth-start", {
      params: { path: { assistant_id: assistantId } },
    })),
    onSuccess: ({ url }) => window.location.assign(url),
    onError: (caught) => setError(caught instanceof Error ? caught.message : "Unable to connect Gmail."),
  });
  const startCalendarOAuth = useMutation({
    mutationFn: () => unwrap(api.POST("/assistants/{assistant_id}/channels/gmail/oauth-start", {
      params: { path: { assistant_id: assistantId } },
      body: { features: ["calendar"] },
    })),
    onSuccess: ({ url }) => window.location.assign(url),
    onError: (caught) => setError(caught instanceof Error ? caught.message : "Unable to connect Calendar."),
  });
  const channels = useQuery({
    queryKey: ["assistant-channels", assistantId],
    queryFn: () => unwrap(api.GET("/assistants/{assistant_id}/channels", { params: { path: { assistant_id: assistantId } } })),
  });
  const calendarConnected = channels.data?.items.some((channel) => (
    channel.kind === "gmail" && channel.google_features.includes("calendar")
  )) ?? false;
  const add = useMutation({
    mutationFn: () => unwrap(api.POST("/assistants/{assistant_id}/channels", {
      params: { path: { assistant_id: assistantId } },
      body: { kind, external_id: externalId, settings: {} },
    })),
    onSuccess: async () => {
      setExternalId("");
      setError("");
      await queryClient.invalidateQueries({ queryKey: ["assistant-channels", assistantId] });
    },
    onError: (caught) => setError(caught instanceof Error ? caught.message : "Unable to add channel."),
  });
  return <section className="panel stack" aria-labelledby="assistant-channels-heading">
    <h2 id="assistant-channels-heading">Channels</h2>
    <p className="muted">Connect a phone number, WhatsApp phone number ID, or Gmail inbox.</p>
    {channels.isLoading && <LoadingState />}
    {channels.error && <ErrorState message="Channel connections could not be loaded." />}
    {channels.data?.items.map((channel) => <p key={channel.id}>{channel.kind}: <strong>{channel.external_id}</strong></p>)}
    {gmailMode.data?.mode === "google" && <div className="stack">
      <p className="help">Gmail is draft-only. Replies are saved as drafts for review and are never sent automatically.</p>
      <button className="button-primary" type="button" disabled={startGmailOAuth.isPending} onClick={() => startGmailOAuth.mutate()}>
        {startGmailOAuth.isPending ? "Connecting…" : "Connect Gmail"}
      </button>
    </div>}
    {gmailMode.data?.mode === "off" && <p className="help">Gmail is not enabled for this deployment.</p>}
    {bookingEnabled && calendarMode.data?.mode === "off" && <p className="help">Calendar booking is not enabled for this deployment. Ask your administrator to enable Calendar integration.</p>}
    {bookingEnabled && calendarMode.data?.mode === "fake" && <p className="help">
      The fake Calendar provider is enabled for this deployment. {calendarConnected
        ? "Google Calendar is connected for this assistant."
        : "Google Calendar is not connected for this assistant."}
    </p>}
    {bookingEnabled && calendarMode.data?.mode === "google" && <>
      {gmailMode.data?.mode !== "google" && <p className="help" role="alert">Google Calendar requires Gmail Google mode to be enabled for this deployment.</p>}
      {gmailMode.data?.mode === "google" && <>
        {calendarConnected
          ? <p className="help">Google Calendar is connected for this assistant.</p>
          : <p className="help" role="alert">Calendar is disconnected. Connect Google Calendar to enable live booking.</p>}
        {!calendarConnected && <button className="button-primary" type="button" disabled={startCalendarOAuth.isPending} onClick={() => startCalendarOAuth.mutate()}>
          {startCalendarOAuth.isPending ? "Connecting…" : "Connect Google Calendar"}
        </button>}
      </>}
    </>}
    {gmailMode.data?.mode !== "off" && (gmailMode.data?.mode !== "google" || kind !== "gmail") && <form className="form-grid" onSubmit={(event) => {
      event.preventDefault();
      setError("");
      add.mutate();
    }}>
      <Field path="channel-kind" label="Channel">{(fieldId) => <select id={fieldId} value={kind} onChange={(event) => setKind(event.target.value as typeof kind)}>
        <option value="phone">Phone</option><option value="whatsapp">WhatsApp</option>{gmailMode.data?.mode === "fake" && <option value="gmail">Gmail</option>}<option value="web">Web</option>
      </select>}</Field>
      <Field path="channel-external-id" label={kind === "whatsapp" ? "WhatsApp phone_number_id" : kind === "gmail" ? "Mailbox email address" : "External ID"}>{(fieldId) => <input id={fieldId} value={externalId} onChange={(event) => setExternalId(event.target.value)} required />}</Field>
      <button className="button-primary" type="submit" disabled={add.isPending || !externalId.trim()}>{add.isPending ? "Adding…" : "Add channel"}</button>
    </form>}
    {kind === "whatsapp" && <p className="help">Enter the phone_number_id from your Meta WhatsApp Business account.</p>}
    {kind === "gmail" && <p className="help">Enter the mailbox email address. Drafts are created for review; mail is never sent automatically.</p>}
    {error && <p role="alert">{error}</p>}
  </section>;
}

export function AssistantKnowledgePage() {
  const { id = "" } = useParams();
  const queryClient = useQueryClient();
  const assistantQuery = useQuery({ queryKey: ["assistant", id], queryFn: () => unwrap(api.GET("/assistants/{assistant_id}", { params: { path: { assistant_id: id } } })) });
  const sourcesQuery = useQuery({ queryKey: ["knowledge-sources", id], queryFn: () => unwrap(api.GET("/assistants/{assistant_id}/knowledge/sources", { params: { path: { assistant_id: id } } })) });
  const digestQuery = useQuery({ queryKey: ["knowledge-digest", id], queryFn: () => unwrap(api.GET("/assistants/{assistant_id}/knowledge/digest", { params: { path: { assistant_id: id } } })) });
  const [url, setUrl] = useState("");
  const [urlError, setUrlError] = useState("");
  const [fileError, setFileError] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [digest, setDigest] = useState("");
  const [jobId, setJobId] = useState("");
  const [status, setStatus] = useState("");
  const [faqs, setFaqs] = useState<Schemas["KnowledgeFAQ"][]>([]);
  useEffect(() => { if (digestQuery.data) setDigest(digestQuery.data.content); }, [digestQuery.data]);
  useEffect(() => { if (assistantQuery.data) setFaqs(structuredClone(assistantQuery.data.config.knowledge?.faqs ?? [])); }, [assistantQuery.data]);
  const jobQuery = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => unwrap(api.GET("/jobs/{job_id}", { params: { path: { job_id: jobId ?? "" } } })),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const state = query.state.data?.status;
      return state === "queued" || state === "running" ? 1000 : false;
    },
  });
  useEffect(() => {
    if (!jobQuery.data) return;
    setStatus(jobQuery.data.status === "failed" ? `Import failed: ${jobQuery.data.error ?? "Unknown error"}` : `Import ${jobQuery.data.status}`);
    if (jobQuery.data.status === "succeeded") {
      void queryClient.invalidateQueries({ queryKey: ["knowledge-sources", id] });
      void queryClient.invalidateQueries({ queryKey: ["knowledge-digest", id] });
    }
  }, [jobQuery.data, id, queryClient]);
  const faqSave = useMutation({
    mutationFn: () => {
      if (!assistantQuery.data) throw new Error("Assistant not loaded.");
      const config = structuredClone(assistantQuery.data.config);
      config.knowledge = { ...config.knowledge, faqs, max_digest_chars: config.knowledge?.max_digest_chars ?? 24000 };
      return unwrap(api.PUT("/assistants/{assistant_id}", { params: { path: { assistant_id: id } }, body: { name: assistantQuery.data.name, template: assistantQuery.data.template, config } }));
    },
    onSuccess: () => { setStatus("FAQ saved"); void queryClient.invalidateQueries({ queryKey: ["assistant", id] }); },
  });
  const digestSave = useMutation({
    mutationFn: () => unwrap(api.PUT("/assistants/{assistant_id}/knowledge/digest", { params: { path: { assistant_id: id } }, body: { content: digest } })),
    onSuccess: () => { setStatus("Digest saved"); void queryClient.invalidateQueries({ queryKey: ["knowledge-digest", id] }); },
  });
  const sourceAdd = useMutation({
    mutationFn: (source: { uri: string }) => unwrap(api.POST("/assistants/{assistant_id}/knowledge/sources", { params: { path: { assistant_id: id } }, body: { kind: "url", uri: source.uri } })),
    onSuccess: () => { setUrl(""); setUrlError(""); setStatus("Website added"); void queryClient.invalidateQueries({ queryKey: ["knowledge-sources", id] }); },
  });
  const upload = useMutation({
    mutationFn: (uploadFile: File) => uploadKnowledgeFile(id, uploadFile),
    onSuccess: () => { setFile(null); setFileError(""); setStatus("File uploaded"); void queryClient.invalidateQueries({ queryKey: ["knowledge-sources", id] }); },
  });
  const importKnowledge = useMutation({
    mutationFn: () => unwrap(api.POST("/assistants/{assistant_id}/knowledge/import", { params: { path: { assistant_id: id } } })),
    onSuccess: (job) => { setJobId(job.id); setStatus(`Import ${job.status}`); },
  });
  if (assistantQuery.isLoading) return <main className="page" id="main-content"><LoadingState /></main>;
  if (assistantQuery.error || !assistantQuery.data) return <main className="page" id="main-content"><ErrorState message="Knowledge settings could not be loaded." /></main>;
  const maxChars = assistantQuery.data.config.knowledge?.max_digest_chars ?? 24000;
  return <main className="page" id="main-content">
    <p className="eyebrow">Assistant knowledge</p><h1>{assistantQuery.data.name}</h1>
    <section className="panel stack"><h2>Knowledge sources</h2>
      {sourcesQuery.isLoading && <LoadingState />}
      {sourcesQuery.data?.items.map((source) => <div className="list-row" key={source.id}><div><strong>{source.kind}</strong> · {source.uri}<br /><span className="muted">{source.fetched_at ? `Fetched ${formatDate(source.fetched_at)}` : "Not imported yet"}</span></div><Badge tone={source.status === "succeeded" ? "success" : "warning"}>{source.status}</Badge></div>)}
      {!sourcesQuery.isLoading && !sourcesQuery.data?.items.length && <EmptyState>No knowledge sources yet.</EmptyState>}
      <form className="toolbar" onSubmit={(event) => { event.preventDefault(); if (!isHttpUrl(url)) { setUrlError("Enter a valid http or https URL."); return; } sourceAdd.mutate({ uri: url }); }}>
        <div className="field"><label htmlFor="website-url">Website URL</label><input id="website-url" type="url" value={url} onChange={(event) => setUrl(event.target.value)} aria-invalid={Boolean(urlError)} aria-describedby={urlError ? "website-url-error" : undefined} /></div>
        <button type="submit">Add website</button>
        <FieldError id="website-url-error">{urlError}</FieldError>
      </form>
      <div className="field"><label htmlFor="knowledge-file">Upload a document</label><input id="knowledge-file" type="file" accept=".pdf,.docx,.md,.txt" onChange={(event: ChangeEvent<HTMLInputElement>) => {
        const selectedFile = event.target.files?.[0] ?? null;
        setFile(selectedFile);
        const extension = selectedFile?.name.split(".").at(-1)?.toLowerCase();
        setFileError(selectedFile && !["pdf", "docx", "md", "txt"].includes(extension ?? "") ? "Choose a PDF, DOCX, Markdown, or text file." : "");
      }} aria-invalid={Boolean(fileError)} aria-describedby={fileError ? "knowledge-file-error" : undefined} /><FieldError id="knowledge-file-error">{fileError}</FieldError><button type="button" disabled={!file || Boolean(fileError) || upload.isPending} onClick={() => file && upload.mutate(file)}>Upload file</button></div>
      <button type="button" className="button-primary" disabled={importKnowledge.isPending || jobQuery.data?.status === "queued" || jobQuery.data?.status === "running"} onClick={() => importKnowledge.mutate()}>Import knowledge</button>
      <div aria-live="polite" role="status">{status}</div>
    </section>
    <section className="panel stack"><h2>Knowledge digest</h2>
      {digestQuery.isLoading && <LoadingState />}
      <div className="field"><label htmlFor="digest-content">Digest content</label><textarea id="digest-content" maxLength={maxChars} value={digest} onChange={(event) => setDigest(event.target.value)} /><span className="help">{digest.length} / {maxChars} characters</span></div>
      <button type="button" onClick={() => digestSave.mutate()} disabled={digestSave.isPending}>Save digest</button>
    </section>
    <section className="panel stack"><h2>Frequently asked questions</h2>
      {faqs.map((faq, index) => <div className="inline-editor" key={`faq-${index}`}>
        <div className="field"><label htmlFor={`faq-question-${index}`}>Question</label><input id={`faq-question-${index}`} value={faq.question} onChange={(event) => setFaqs((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, question: event.target.value } : item))} /></div>
        <div className="field"><label htmlFor={`faq-answer-${index}`}>Answer</label><textarea id={`faq-answer-${index}`} value={faq.answer} onChange={(event) => setFaqs((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, answer: event.target.value } : item))} /></div>
        <button type="button" onClick={() => setFaqs((current) => current.filter((_, itemIndex) => itemIndex !== index))}>Remove FAQ</button>
      </div>)}
      <div className="button-row"><button type="button" onClick={() => setFaqs((current) => [...current, { question: "", answer: "" }])}>Add FAQ</button><button type="button" className="button-primary" onClick={() => faqSave.mutate()} disabled={faqSave.isPending}>Save FAQs</button></div>
    </section>
  </main>;
}
