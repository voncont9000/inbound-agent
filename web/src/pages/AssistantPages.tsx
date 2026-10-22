import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, NavLink, Navigate, useNavigate, useParams } from "react-router-dom";
import { api, unwrap, unwrapNoContent, type Schemas } from "../api/client";
import { ApiError, errorMessage, fieldErrorsFrom422 } from "../api/errors";
import { useAuth } from "../auth/AuthProvider";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { SettingsTabContent } from "../components/settings/SettingsTabContent";
import type { AssistantConfig } from "../lib/config";
import { validateAssistantConfig } from "../lib/config";
import { isSettingsTab, settingsTabPath, settingsTabs, tabForPath, type SettingsTabId } from "../lib/settingsTabs";
import { Badge, DeleteIcon, EmptyState, ErrorState, ErrorSummary, LoadingState, PageHeader } from "../components/States";
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

const CUSTOM_TEMPLATE = "__custom__";

const templateLabel = (template: string | null | undefined) => {
  if (!template) return "Custom";
  const words = template.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
};

const switcherChannelIcon = (kind: string) => {
  const paths: Record<string, string> = {
    phone: "M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z",
    web: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10",
    gmail: "M4 5h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2m18 2-10 7L2 7",
    whatsapp: "M21 11.5a8.4 8.4 0 0 1-12.1 7.7L3 21l1.8-5.7A8.4 8.4 0 1 1 21 11.5ZM8.5 11.5h.01M12 11.5h.01M15.5 11.5h.01",
  };
  const path = paths[kind];
  return path ? <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d={path} /></svg> : null;
};

function AssistantSwitcherItem({ assistant, activeTab, current, onSelect }: {
  assistant: { id: string; name: string };
  activeTab: SettingsTabId;
  current: boolean;
  onSelect: () => void;
}) {
  const channels = useQuery({
    queryKey: ["assistant-channels", assistant.id],
    queryFn: () => unwrap(api.GET("/assistants/{assistant_id}/channels", { params: { path: { assistant_id: assistant.id } } })),
  });
  const kinds = [...new Set(channels.data?.items.map(({ kind }) => kind) ?? [])];
  const phone = channels.data?.items.find(({ kind }) => kind === "phone")?.external_id;
  return <Link className="assistant-switcher-item" to={settingsTabPath(assistant.id, activeTab)} aria-current={current ? "page" : undefined} onClick={onSelect}>
    <span className="assistant-switcher-item-text"><span>{assistant.name}</span><small>{phone ?? "No number"}</small></span>
    <span className="assistant-switcher-channel-icons">{kinds.map((kind) => <span key={kind} aria-label={kind}>{switcherChannelIcon(kind)}</span>)}</span>
  </Link>;
}

function AssistantSwitcher({ assistantId, assistantName, activeTab }: {
  assistantId: string;
  assistantName: string;
  activeTab: SettingsTabId;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const switcherRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const assistants = useQuery({
    queryKey: ["assistants"],
    queryFn: () => unwrap(api.GET("/assistants")),
  });
  const channels = useQuery({
    queryKey: ["assistant-channels", assistantId],
    queryFn: () => unwrap(api.GET("/assistants/{assistant_id}/channels", { params: { path: { assistant_id: assistantId } } })),
  });
  const phone = channels.data?.items.find(({ kind }) => kind === "phone")?.external_id;
  const term = search.trim().toLocaleLowerCase();
  const visible = assistants.data?.items.filter((assistant) => assistant.name.toLocaleLowerCase().includes(term)) ?? [];
  useEffect(() => {
    if (!open) return;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (event.target instanceof Node && !switcherRef.current?.contains(event.target)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsidePointer);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);
  return <div className="assistant-switcher" ref={switcherRef}>
    <button ref={triggerRef} type="button" className="assistant-switcher-trigger" aria-label="Switch assistant" aria-expanded={open} aria-controls="assistant-switcher-popover" onClick={() => setOpen((value) => !value)}>
      <span className="assistant-switcher-trigger-text"><strong>{assistantName}</strong><span>{phone ?? "No number"}</span></span>
      <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="m7 10 5 5 5-5" /></svg>
    </button>
    {open && <div className="assistant-switcher-popover" id="assistant-switcher-popover">
      <label className="sr-only" htmlFor="assistant-switcher-search">Search assistants</label>
      <input id="assistant-switcher-search" type="search" placeholder="Search assistants" value={search} onChange={(event) => setSearch(event.target.value)} />
      {assistants.isLoading && <LoadingState />}
      {assistants.error && <ErrorState message="Assistants could not be loaded." />}
      <div className="assistant-switcher-list">
        {visible.map((assistant) => <AssistantSwitcherItem key={assistant.id} assistant={assistant} activeTab={activeTab} current={assistant.id === assistantId} onSelect={() => setOpen(false)} />)}
        {!assistants.isLoading && visible.length === 0 && <p className="muted">No assistants found.</p>}
      </div>
      <Link className="assistant-switcher-new" to="/assistants/new" onClick={() => setOpen(false)}>New assistant</Link>
    </div>}
  </div>;
}

export function AssistantsPage() {
  const navigate = useNavigate();
  const { tenant, user, memberships, selectedTenant } = useAuth();
  const role = memberships.find(({ id }) => id === selectedTenant)?.role ?? tenant?.role;
  const canDelete = Boolean(user) && role === "owner";
  const [selectedTemplate, setSelectedTemplate] = useState("");
  const [assistantName, setAssistantName] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [error, setError] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Schemas["AssistantResponse"] | null>(null);
  const [deleteError, setDeleteError] = useState("");
  const queryClient = useQueryClient();
  const assistants = useQuery({ queryKey: ["assistants"], queryFn: () => unwrap(api.GET("/assistants")) });
  const templatesQuery = useQuery({ queryKey: ["templates"], queryFn: () => unwrap(api.GET("/templates")), enabled: showCreate });
  const isCustom = selectedTemplate === CUSTOM_TEMPLATE;
  const [customForm, setCustomForm] = useState({ sector: "", use_case: "", details: "" });
  const customOptions = useQuery({
    queryKey: ["custom-template-options"],
    queryFn: () => unwrap(api.GET("/templates/custom-options")),
    enabled: showCreate && isCustom,
  });
  useEffect(() => {
    if (!customOptions.data) return;
    setCustomForm((form) => ({
      ...form,
      sector: form.sector || customOptions.data.sectors[0] || "",
      use_case: form.use_case || customOptions.data.use_cases[0] || "",
    }));
  }, [customOptions.data]);
  const customDraft = useMutation({
    mutationFn: (input: Schemas["CustomTemplateRequest"]) => unwrap(api.POST("/templates/custom-draft", { body: input })),
    onError: (caught) => setError(caught instanceof Error ? caught.message : "The template could not be generated."),
  });
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
  const remove = useMutation({
    mutationFn: (assistant: Schemas["AssistantResponse"]) => unwrapNoContent(api.DELETE(
      "/assistants/{assistant_id}",
      {
        params: {
          path: { assistant_id: assistant.id },
          ...(assistant.conversation_count > 0
            ? { query: { delete_conversations: true } }
            : {}),
        },
      },
    )),
    onSuccess: async () => {
      setDeleteTarget(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["assistants"] }),
        queryClient.invalidateQueries({ queryKey: ["workspace-channels"] }),
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
        queryClient.invalidateQueries({ queryKey: ["conversation"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] }),
        queryClient.invalidateQueries({ queryKey: ["contacts"] }),
        queryClient.invalidateQueries({ queryKey: ["tasks"] }),
      ]);
    },
    onError: (caught) => setDeleteError(
      caught instanceof ApiError
        ? errorMessage(caught.body, caught.message)
        : caught instanceof Error
          ? caught.message
          : "Unable to delete assistant.",
    ),
  });
  const selected = templatesQuery.data?.find(({ name }) => name === selectedTemplate);
  return <main className="page" id="main-content">
    <PageHeader title="Assistants" lede="Each assistant answers for one business, with its own voice, rules and knowledge." actions={<>
      <button className="button-primary" type="button" onClick={() => setShowCreate((value) => !value)}>Create from template</button>
      <Link className="button-primary" to="/assistants/new">Set up from website</Link>
    </>} />
    {showCreate && <section className="panel" aria-labelledby="create-assistant-heading">
      <h2 id="create-assistant-heading">Create an assistant</h2>
      {templatesQuery.isLoading && <LoadingState />}
      {templatesQuery.error && <ErrorState message="Templates could not be loaded." />}
      <form className="stack" onSubmit={(event) => {
        event.preventDefault();
        if (isCustom) {
          if (!customDraft.data) { setError("Generate the template first."); return; }
          setError("");
          create.mutate({ name: assistantName, template: null, config: customDraft.data.config });
          return;
        }
        if (!selected) { setError("Choose a template."); return; }
        setError("");
        create.mutate({ name: assistantName, template: selected.name, config: selected.config });
      }}>
        <fieldset className="stack"><legend>Template</legend>
          {(templatesQuery.data ?? []).map((template) => <label key={template.name} className="radio-row">
            <input type="radio" name="template" value={template.name} checked={selectedTemplate === template.name} onChange={() => setSelectedTemplate(template.name)} />
            <span><strong>{template.label}</strong><br /><span className="muted">{template.description}</span></span>
          </label>)}
          <label className="radio-row">
            <input type="radio" name="template" value={CUSTOM_TEMPLATE} checked={isCustom} onChange={() => setSelectedTemplate(CUSTOM_TEMPLATE)} />
            <span><strong>Custom template</strong><br /><span className="muted">Generated for your sector and use case. Review it, then adjust anything in settings.</span></span>
          </label>
        </fieldset>
        {isCustom && <div className="stack custom-template">
          {customOptions.isLoading && <LoadingState />}
          {customOptions.error && <ErrorState message="Sectors and use cases could not be loaded." />}
          {customOptions.data && <>
            <div className="field-grid">
              <div className="field">
                <label htmlFor="custom-template-sector">Sector</label>
                <select id="custom-template-sector" value={customForm.sector} onChange={(event) => setCustomForm({ ...customForm, sector: event.target.value })}>
                  {customOptions.data.sectors.map((sector) => <option key={sector} value={sector}>{sector}</option>)}
                </select>
              </div>
              <div className="field">
                <label htmlFor="custom-template-use-case">Use case</label>
                <select id="custom-template-use-case" value={customForm.use_case} onChange={(event) => setCustomForm({ ...customForm, use_case: event.target.value })}>
                  {customOptions.data.use_cases.map((useCase) => <option key={useCase} value={useCase}>{useCase}</option>)}
                </select>
              </div>
            </div>
            <div className="field">
              <label htmlFor="custom-template-details">Anything else (optional)</label>
              <textarea id="custom-template-details" rows={2} maxLength={600} placeholder="For example: we only take bookings by phone, never quote prices" value={customForm.details} onChange={(event) => setCustomForm({ ...customForm, details: event.target.value })} />
            </div>
            <div className="button-row">
              <button className="button-secondary" type="button" disabled={customDraft.isPending || !customForm.sector || !customForm.use_case} onClick={() => customDraft.mutate(customForm)}>
                {customDraft.isPending ? "Generating…" : customDraft.data ? "Generate again" : "Generate template"}
              </button>
            </div>
            {customDraft.data && <section className="panel custom-template-preview" aria-labelledby="custom-template-heading">
              <p className="eyebrow">Generated template</p>
              <h3 id="custom-template-heading">{customDraft.data.label}</h3>
              {customDraft.data.description && <p className="muted">{customDraft.data.description}</p>}
              {customDraft.data.sets_up.length > 0 && <ul>{customDraft.data.sets_up.map((item) => <li key={item}>{item}</li>)}</ul>}
              {customDraft.data.warnings.length > 0 && <ul className="muted">{customDraft.data.warnings.map((item) => <li key={item}>{item}</li>)}</ul>}
            </section>}
          </>}
        </div>}
        {!isCustom && selected && <p className="help template-summary">Starts as <strong>{selected.config.business_name}</strong> with {Object.values(selected.config.services).map((service) => service.label).join(", ") || "no services yet"}. Everything can be changed afterwards.</p>}
        <div className="field"><label htmlFor="new-assistant-name">Assistant name</label><input id="new-assistant-name" required value={assistantName} onChange={(event) => setAssistantName(event.target.value)} /></div>
        {error && <p role="alert">{error}</p>}
        <button className="button-primary" type="submit" disabled={create.isPending || (isCustom ? !customDraft.data : !selected)}>Create assistant</button>
      </form>
    </section>}
    {assistants.isLoading && <LoadingState />}
    {assistants.error && <ErrorState message="Assistants could not be loaded." />}
    {!assistants.isLoading && !assistants.error && !assistants.data?.items.length && <EmptyState>No assistants yet.</EmptyState>}
    <div className="card-grid">{assistants.data?.items.map((assistant) => <article className="panel assistant-card" key={assistant.id}>
      <p className="eyebrow">{templateLabel(assistant.template)}</p>
      <h2><Link className="assistant-card-link" to={settingsTabPath(assistant.id)}>{assistant.name}</Link></h2>
      <p>{assistant.config.business_name}</p>
      <p className="muted">{Object.values(assistant.config.services).map((service) => service.label).join(" · ")}</p>
      {canDelete && <button type="button" className="button-danger assistant-card-delete" aria-label={`Delete ${assistant.name}`} title="Delete" onClick={() => {
        setDeleteError("");
        setDeleteTarget(assistant);
      }}><DeleteIcon /></button>}
    </article>)}</div>
    {deleteTarget && <ConfirmDialog
      title={`Delete ${deleteTarget.name}?`}
      confirmLabel="Delete"
      tone="danger"
      onCancel={() => {
        setDeleteTarget(null);
        setDeleteError("");
      }}
      onConfirm={() => remove.mutate(deleteTarget)}
    >
      <p>{deleteTarget.conversation_count > 0
        ? `This also permanently deletes its ${deleteTarget.conversation_count} conversation${deleteTarget.conversation_count === 1 ? "" : "s"}, including transcripts and recordings. Contacts are kept. This can't be undone.`
        : "This can't be undone."}</p>
      <p>Its phone numbers, inboxes and WhatsApp numbers stay on the Channels page, unassigned.</p>
      {deleteError && <p role="alert">{deleteError}</p>}
    </ConfirmDialog>}
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
      <div className="page-header-text"><h1>{baseline.name}</h1></div>
      <AssistantSwitcher assistantId={id} assistantName={baseline.name} activeTab={activeTab} />
    </div>
    <div className="settings-tab-row save-bar" role="region" aria-label="Settings sections and save">
      <nav className="settings-tabs" aria-label="Settings sections">
        {settingsTabs.map(({ id: tabId, label }) => <NavLink key={tabId} to={settingsTabPath(id, tabId)} aria-current={tabId === activeTab ? "page" : undefined}>
          {label}{errorCount(tabId) > 0 && <Badge tone="warning">{`${errorCount(tabId)} to fix`}</Badge>}
        </NavLink>)}
      </nav>
      <div className="settings-save" title="One save covers every tab.">
        <span className={isDirty ? "dirty-indicator" : "muted"} aria-live="polite" role="status">{saveMessage || (isDirty ? "Unsaved changes" : "All changes saved")}</span>
        <button type="button" className="button-primary" onClick={saveConfig} disabled={save.isPending}>{save.isPending ? "Saving…" : "Save changes"}</button>
        <HelpLink to={settingsTabHelp[activeTab]} />
      </div>
    </div>
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
