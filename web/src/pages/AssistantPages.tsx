import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, NavLink, Navigate, useNavigate, useParams } from "react-router-dom";
import { api, unwrap, type Schemas } from "../api/client";
import { ApiError, fieldErrorsFrom422 } from "../api/errors";
import { TestCallButton } from "../components/TestCallButton";
import { SettingsTabContent } from "../components/settings/SettingsTabContent";
import type { AssistantConfig } from "../lib/config";
import { validateAssistantConfig } from "../lib/config";
import { isSettingsTab, settingsTabPath, settingsTabs, tabForPath, type SettingsTabId } from "../lib/settingsTabs";
import { Badge, EmptyState, ErrorState, ErrorSummary, LoadingState, PageHeader } from "../components/States";
import { HelpLink } from "../help/HelpLink";
import { settingsTabHelp } from "../help/settingsTabHelp";

function prepareConfigForSave(config: AssistantConfig): AssistantConfig {
  const result = structuredClone(config);
  Object.values(result.services).forEach((service) => service.fields.forEach((field) => {
    if (field.choices) field.choices = field.choices.map((choice) => choice.trim()).filter(Boolean);
  }));
  result.urgency_rules.forEach((rule) => {
    if (rule.type === "keyword") rule.phrases = rule.phrases.map((phrase) => phrase.trim()).filter(Boolean);
    if (rule.type === "sentence") rule.text = rule.text.trim();
  });
  (result.rules?.pronunciations ?? []).forEach((entry) => {
    entry.term = entry.term.trim();
    entry.say_as = entry.say_as.trim();
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
  useEffect(() => {
    if (showCreate && !selectedTemplate && templatesQuery.data?.length) {
      setSelectedTemplate(
        templatesQuery.data.find(({ name }) => name === "blank")?.name ??
          templatesQuery.data[0].name,
      );
    }
  }, [selectedTemplate, showCreate, templatesQuery.data]);
  const create = useMutation({
    mutationFn: (input: Schemas["AssistantCreate"]) => unwrap(api.POST("/assistants", { body: input })),
    onSuccess: (assistant) => navigate(settingsTabPath(assistant.id)),
    onError: (caught) => setError(caught instanceof Error ? caught.message : "Unable to create assistant."),
  });
  const selected = templatesQuery.data?.find(({ name }) => name === selectedTemplate);
  return <main className="page" id="main-content">
    <PageHeader eyebrow="Configuration" title="Assistants" lede="Each assistant answers for one business, with its own voice, rules and knowledge." />
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
            <span><strong>{template.label}</strong><br /><span className="muted">{template.description}</span></span>
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
        <Link to={settingsTabPath(assistant.id)}>Manage assistant</Link>
        <TestCallButton assistantId={assistant.id} />
      </div>
    </article>)}</div>
  </main>;
}

export function AssistantSettingsRedirect({ tab = "general" }: { tab?: SettingsTabId }) {
  const { id = "" } = useParams();
  return <Navigate to={settingsTabPath(id, tab)} replace />;
}

export function AssistantSettingsPage() {
  const { id = "", tab } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const assistantQuery = useQuery({ queryKey: ["assistant", id], queryFn: () => unwrap(api.GET("/assistants/{assistant_id}", { params: { path: { assistant_id: id } } })) });
  const schemaQuery = useQuery({ queryKey: ["assistant-config-schema"], queryFn: () => unwrap(api.GET("/assistant-config/schema")) });
  const [config, setConfig] = useState<AssistantConfig | null>(null);
  const [assistantName, setAssistantName] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saveMessage, setSaveMessage] = useState("");
  const activeTab: SettingsTabId = isSettingsTab(tab) ? tab : "general";
  useEffect(() => {
    if (!assistantQuery.data) return;
    setConfig(structuredClone(assistantQuery.data.config));
    setAssistantName(assistantQuery.data.name);
  }, [assistantQuery.data]);
  const showTabWithErrors = (found: Record<string, string>) => {
    const paths = Object.keys(found).filter((path) => path !== "_form");
    if (paths.length && !paths.some((path) => tabForPath(path) === activeTab)) navigate(settingsTabPath(id, tabForPath(paths[0])));
  };

  const save = useMutation({
    mutationFn: (payload: Schemas["AssistantUpdate"]) => unwrap(api.PUT("/assistants/{assistant_id}", { params: { path: { assistant_id: id } }, body: payload })),
    onSuccess: async (assistant) => {
      setConfig(structuredClone(assistant.config));
      setAssistantName(assistant.name);
      setErrors({});
      setSaveMessage("Changes saved");
      await queryClient.invalidateQueries({ queryKey: ["assistant", id] });
    },
    onError: (caught) => {
      if (caught instanceof ApiError && caught.status === 422) {
        const found = fieldErrorsFrom422(caught.body);
        setErrors(found);
        showTabWithErrors(found);
      } else setErrors({ _form: caught instanceof Error ? caught.message : "Unable to save changes." });
    },
  });
  if (!isSettingsTab(tab)) return <Navigate to={settingsTabPath(id)} replace />;
  if (assistantQuery.error) return <main className="page" id="main-content"><ErrorState message="Assistant settings could not be loaded." /></main>;
  if (assistantQuery.isLoading || !config) return <main className="page" id="main-content"><LoadingState /></main>;
  const baseline = assistantQuery.data!;
  const allErrors = { ...validateAssistantConfig(config), ...errors };
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

  function saveConfig() {
    if (!config) return;
    const payloadConfig = prepareConfigForSave(config);
    const validation = validateAssistantConfig(payloadConfig);
    setErrors(validation);
    setSaveMessage("");
    if (Object.keys(validation).length) {
      showTabWithErrors(validation);
      window.setTimeout(() => document.querySelector<HTMLElement>(".error-summary a")?.focus());
      return;
    }
    save.mutate({ name: assistantName, template: baseline.template, config: payloadConfig });
  }

  const fieldErrors = Object.entries(allErrors).filter(([path]) => path !== "_form");
  const errorCount = (tabId: SettingsTabId) => fieldErrors.filter(([path]) => tabForPath(path) === tabId).length;
  const tabErrors = Object.fromEntries(fieldErrors.filter(([path]) => tabForPath(path) === activeTab));
  const otherTabsWithErrors = settingsTabs.filter(({ id: tabId }) => tabId !== activeTab && errorCount(tabId) > 0);
  return <main className="page" id="main-content">
    <div className="settings-header">
      <div className="page-header-text"><p className="eyebrow">Assistant settings</p><h1>{baseline.name}</h1></div>
      <TestCallButton assistantId={baseline.id} />
    </div>
    <div className="save-bar" role="region" aria-label="Save changes">
      <span className={isDirty ? "dirty-indicator" : "muted"}>{isDirty ? "Unsaved changes" : "All changes saved"}</span>
      <div className="button-row">
        <span className="help">One save covers every tab.</span>
        <button type="button" className="button-primary" onClick={saveConfig} disabled={save.isPending}>{save.isPending ? "Saving…" : "Save changes"}</button>
        <span aria-live="polite" role="status">{saveMessage}</span>
      </div>
    </div>
    <nav className="settings-tabs" aria-label="Settings sections">
      {settingsTabs.map(({ id: tabId, label }) => <NavLink key={tabId} to={settingsTabPath(id, tabId)} aria-current={tabId === activeTab ? "page" : undefined}>
        {label}{errorCount(tabId) > 0 && <Badge tone="warning">{`${errorCount(tabId)} to fix`}</Badge>}
      </NavLink>)}
    </nav>
    <HelpLink to={settingsTabHelp[activeTab]} />
    <div className="stack">
      <ErrorSummary errors={tabErrors} />
      {otherTabsWithErrors.length > 0 && <p role="alert">Also fix: {otherTabsWithErrors.map(({ id: tabId, label }, index) => <span key={tabId}>{index > 0 && ", "}<Link to={settingsTabPath(id, tabId)}>{label}</Link></span>)}</p>}
      {allErrors._form && <p role="alert">{allErrors._form}</p>}
      <SettingsTabContent
        tab={activeTab}
        assistantId={id}
        config={config}
        saved={baseline.config}
        update={update}
        errors={allErrors}
        clearFieldError={clearFieldError}
        schema={schemaQuery.data}
        assistantName={assistantName}
        setAssistantName={(name) => { setAssistantName(name); setSaveMessage(""); }}
      />
    </div>
  </main>;
}
