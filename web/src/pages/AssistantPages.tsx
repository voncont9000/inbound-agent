import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, NavLink, Navigate, useLocation, useNavigate, useParams } from "react-router-dom";
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

type AssistantSettingsDraft = { config: AssistantConfig; name: string };
type SettingsSaveStatus = "saved" | "dirty" | "saving" | "invalid" | "retrying" | "failed";

function draftsEqual(first: AssistantSettingsDraft, second: AssistantSettingsDraft) {
  return first.name === second.name && JSON.stringify(first.config) === JSON.stringify(second.config);
}

function draftIsDirty(draft: AssistantSettingsDraft, baseline: { config: AssistantConfig; name: string }) {
  return JSON.stringify(draft.config) !== JSON.stringify(baseline.config) || draft.name !== baseline.name;
}

function isRetryableSaveError(error: unknown) {
  return !(error instanceof ApiError) || error.status >= 500;
}

export function AssistantSettingsPage() {
  const { id = "", tab } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const assistantQuery = useQuery({ queryKey: ["assistant", id], queryFn: () => unwrap(api.GET("/assistants/{assistant_id}", { params: { path: { assistant_id: id } } })) });
  const schemaQuery = useQuery({ queryKey: ["assistant-config-schema"], queryFn: () => unwrap(api.GET("/assistant-config/schema")) });
  const [config, setConfig] = useState<AssistantConfig | null>(null);
  const [assistantName, setAssistantName] = useState("");
  const [baseline, setBaseline] = useState<typeof assistantQuery.data>(undefined);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saveStatus, setSaveStatus] = useState<SettingsSaveStatus>("saved");
  const [hasPendingSave, setHasPendingSave] = useState(false);
  const baselineRef = useRef<typeof assistantQuery.data>(undefined);
  const draftRef = useRef<AssistantSettingsDraft | null>(null);
  const loadedAssistantIdRef = useRef<string | null>(null);
  const mountedRef = useRef(false);
  const debounceTimerRef = useRef<number | null>(null);
  const retryTimerRef = useRef<number | null>(null);
  const savePromiseRef = useRef<Promise<boolean> | null>(null);
  const autosaveAfterFlightRef = useRef(false);
  const interactiveSaveRequestedRef = useRef(false);
  const saveCurrentRef = useRef<(attempt?: number, interactive?: boolean) => Promise<boolean>>(async () => false);
  const flushPendingRef = useRef<() => void>(() => {});
  const activeTab: SettingsTabId = isSettingsTab(tab) ? tab : "general";
  const showTabWithErrors = (found: Record<string, string>) => {
    const paths = Object.keys(found).filter((path) => path !== "_form");
    if (paths.length && !paths.some((path) => tabForPath(path) === activeTab)) navigate(settingsTabPath(id, tabForPath(paths[0])));
  };

  const save = useMutation({
    mutationFn: ({ payload }: { payload: Schemas["AssistantUpdate"]; sentDraft: AssistantSettingsDraft; attempt: number; interactive: boolean }) => unwrap(api.PUT("/assistants/{assistant_id}", { params: { path: { assistant_id: id } }, body: payload })),
    onSuccess: (assistant, { sentDraft }) => {
      baselineRef.current = assistant;
      queryClient.setQueryData(["assistant", id], assistant);
      const unchanged = draftRef.current !== null && draftsEqual(draftRef.current, sentDraft);
      if (mountedRef.current) {
        setBaseline(assistant);
        if (unchanged) {
          const savedDraft = { config: structuredClone(assistant.config), name: assistant.name };
          draftRef.current = savedDraft;
          setConfig(savedDraft.config);
          setAssistantName(savedDraft.name);
          setErrors({});
          setSaveStatus("saved");
        } else {
          setSaveStatus("dirty");
        }
      }
      void queryClient.invalidateQueries({ queryKey: ["assistant", id] });
    },
    onError: (caught, { attempt, interactive }) => {
      if (!mountedRef.current) return;
      if (caught instanceof ApiError && caught.status === 422) {
        const found = fieldErrorsFrom422(caught.body);
        setErrors(found);
        setSaveStatus("invalid");
        setHasPendingSave(false);
        if (interactive || interactiveSaveRequestedRef.current) {
          showTabWithErrors(found);
          window.setTimeout(() => document.querySelector<HTMLElement>(".error-summary a")?.focus());
        }
      } else if (attempt === 0 && isRetryableSaveError(caught)) {
        setSaveStatus("retrying");
        setHasPendingSave(true);
        if (retryTimerRef.current !== null) window.clearTimeout(retryTimerRef.current);
        retryTimerRef.current = window.setTimeout(() => {
          retryTimerRef.current = null;
          void saveCurrentRef.current(1);
        }, 3000);
      } else {
        setSaveStatus("failed");
        setHasPendingSave(true);
      }
    },
  });

  const setPendingSave = (pending: boolean) => {
    if (mountedRef.current) setHasPendingSave(pending);
  };
  const clearDebounceTimer = () => {
    if (debounceTimerRef.current === null) return;
    window.clearTimeout(debounceTimerRef.current);
    debounceTimerRef.current = null;
  };
  const clearRetryTimer = () => {
    if (retryTimerRef.current === null) return;
    window.clearTimeout(retryTimerRef.current);
    retryTimerRef.current = null;
  };
  const currentDraftIsDirty = () => {
    const draft = draftRef.current;
    const currentBaseline = baselineRef.current;
    return Boolean(draft && currentBaseline && draftIsDirty(draft, currentBaseline));
  };

  useEffect(() => {
    if (!assistantQuery.data) return;
    if (loadedAssistantIdRef.current !== id) {
      loadedAssistantIdRef.current = id;
      baselineRef.current = assistantQuery.data;
      const loadedDraft = { config: structuredClone(assistantQuery.data.config), name: assistantQuery.data.name };
      draftRef.current = loadedDraft;
      setBaseline(assistantQuery.data);
      setConfig(loadedDraft.config);
      setAssistantName(loadedDraft.name);
      setErrors({});
      setSaveStatus("saved");
      setHasPendingSave(false);
    } else {
      baselineRef.current = assistantQuery.data;
      setBaseline(assistantQuery.data);
    }
  }, [assistantQuery.data, id]);

  const runSave = async (attempt = 0, interactive = false): Promise<boolean> => {
    if (savePromiseRef.current) {
      autosaveAfterFlightRef.current = true;
      if (interactive) interactiveSaveRequestedRef.current = true;
      return savePromiseRef.current;
    }
    const currentBaseline = baselineRef.current;
    const currentDraft = draftRef.current;
    if (!currentBaseline || !currentDraft) return false;
    if (!draftIsDirty(currentDraft, currentBaseline)) {
      setSaveStatus("saved");
      setPendingSave(false);
      return true;
    }

    const payloadConfig = prepareConfigForSave(currentDraft.config);
    const validation = validateAssistantConfig(payloadConfig);
    if (Object.keys(validation).length) {
      if (mountedRef.current) {
        setErrors(validation);
        setSaveStatus("invalid");
        setHasPendingSave(false);
        if (interactive) {
          showTabWithErrors(validation);
          window.setTimeout(() => document.querySelector<HTMLElement>(".error-summary a")?.focus());
        }
      }
      return false;
    }

    interactiveSaveRequestedRef.current = false;
    const sentDraft = { config: structuredClone(currentDraft.config), name: currentDraft.name };
    if (mountedRef.current) {
      setErrors({});
      setSaveStatus("saving");
      setHasPendingSave(true);
    }
    const request = {
      payload: { name: currentDraft.name, template: currentBaseline.template, config: payloadConfig },
      sentDraft,
      attempt,
      interactive,
    };
    const pending = save.mutateAsync(request).then(() => true).catch(() => false).finally(() => {
      savePromiseRef.current = null;
      interactiveSaveRequestedRef.current = false;
      if (autosaveAfterFlightRef.current) {
        autosaveAfterFlightRef.current = false;
        clearRetryTimer();
        if (currentDraftIsDirty()) void saveCurrentRef.current(0);
        else if (mountedRef.current) {
          setSaveStatus("saved");
          setHasPendingSave(false);
        }
        return;
      }
      if (!mountedRef.current) return;
      if (retryTimerRef.current !== null || debounceTimerRef.current !== null) return;
      if (!currentDraftIsDirty()) {
        setSaveStatus("saved");
        setHasPendingSave(false);
      }
    });
    savePromiseRef.current = pending;
    return pending;
  };
  saveCurrentRef.current = runSave;

  const scheduleAutosave = () => {
    clearDebounceTimer();
    clearRetryTimer();
    if (!currentDraftIsDirty() && !savePromiseRef.current) {
      setSaveStatus("saved");
      setPendingSave(false);
      return;
    }
    setSaveStatus("dirty");
    setPendingSave(true);
    debounceTimerRef.current = window.setTimeout(() => {
      debounceTimerRef.current = null;
      if (savePromiseRef.current) {
        autosaveAfterFlightRef.current = true;
        return;
      }
      void saveCurrentRef.current(0);
    }, 1200);
  };
  const flushPendingSave = () => {
    if (debounceTimerRef.current === null) return;
    clearDebounceTimer();
    void saveCurrentRef.current(0);
  };
  flushPendingRef.current = flushPendingSave;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (debounceTimerRef.current !== null) {
        clearDebounceTimer();
        void saveCurrentRef.current(0);
      }
      clearRetryTimer();
    };
  }, []);

  const previousPathRef = useRef(location.pathname);
  useEffect(() => {
    if (previousPathRef.current === location.pathname) return;
    previousPathRef.current = location.pathname;
    flushPendingRef.current();
  }, [location.pathname]);

  useEffect(() => {
    if (!hasPendingSave) return;
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnBeforeUnload);
    return () => window.removeEventListener("beforeunload", warnBeforeUnload);
  }, [hasPendingSave]);

  const update = (change: (draft: AssistantConfig) => void) => {
    const current = draftRef.current?.config ?? config;
    if (!current) return;
    const draft = structuredClone(current);
    change(draft);
    if (draftRef.current) draftRef.current = { ...draftRef.current, config: draft };
    setConfig(draft);
    scheduleAutosave();
  };
  const changeAssistantName = (name: string) => {
    setAssistantName(name);
    if (draftRef.current) draftRef.current = { ...draftRef.current, name };
    scheduleAutosave();
  };
  const clearFieldError = (path: string) => {
    setErrors((current) => {
      const next = { ...current };
      delete next[path];
      return next;
    });
  };

  async function saveDraft(): Promise<boolean> {
    clearDebounceTimer();
    clearRetryTimer();
    if (savePromiseRef.current) {
      interactiveSaveRequestedRef.current = true;
      const saved = await savePromiseRef.current;
      if (!saved) return false;
    }
    clearDebounceTimer();
    if (!currentDraftIsDirty()) {
      setSaveStatus("saved");
      setPendingSave(false);
      return true;
    }
    return saveCurrentRef.current(0, true);
  }

  if (!isSettingsTab(tab)) return <Navigate to={settingsTabPath(id)} replace />;
  if (assistantQuery.error) return <main className="page" id="main-content"><ErrorState message="Assistant settings could not be loaded." /></main>;
  if (assistantQuery.isLoading || !config || !baseline || baseline.id !== id) return <main className="page" id="main-content"><LoadingState /></main>;
  const allErrors = { ...validateAssistantConfig(config), ...errors };
  const isDirty = JSON.stringify(config) !== JSON.stringify(baseline.config) || assistantName !== baseline.name;
  const fieldErrors = Object.entries(allErrors).filter(([path]) => path !== "_form");
  const errorCount = (tabId: SettingsTabId) => fieldErrors.filter(([path]) => tabForPath(path) === tabId).length;
  const tabErrors = Object.fromEntries(fieldErrors.filter(([path]) => tabForPath(path) === activeTab));
  const otherTabsWithErrors = settingsTabs.filter(({ id: tabId }) => tabId !== activeTab && errorCount(tabId) > 0);
  const saveStatusText: Record<SettingsSaveStatus, string> = {
    saved: "All changes saved",
    dirty: "Unsaved changes",
    saving: "Saving…",
    invalid: "Not saved: fix the highlighted fields",
    retrying: "Couldn't save. Retrying…",
    failed: "Couldn't save",
  };
  return <main className="page" id="main-content">
    <div className="settings-header">
      <div className="page-header-text"><h1>{baseline.name}</h1></div>
      <AssistantSwitcher assistantId={id} assistantName={baseline.name} activeTab={activeTab} />
    </div>
    <div className="settings-tab-row save-bar" role="region" aria-label="Settings sections and save status">
      <nav className="settings-tabs" aria-label="Settings sections">
        {settingsTabs.map(({ id: tabId, label }) => <NavLink key={tabId} to={settingsTabPath(id, tabId)} aria-current={tabId === activeTab ? "page" : undefined}>
          {label}{errorCount(tabId) > 0 && <Badge tone="warning">{`${errorCount(tabId)} to fix`}</Badge>}
        </NavLink>)}
      </nav>
      <div className="settings-save">
        <span className={isDirty || saveStatus === "failed" ? "dirty-indicator" : "muted"} aria-live="polite" role="status">{saveStatusText[saveStatus]}</span>
        {saveStatus === "failed" && <button type="button" className="button-link" onClick={() => { clearRetryTimer(); void saveCurrentRef.current(0, true); }}>Retry</button>}
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
        saveDraft={saveDraft}
        errors={allErrors}
        clearFieldError={clearFieldError}
        schema={schemaQuery.data}
        assistantName={assistantName}
        setAssistantName={changeAssistantName}
      />
    </div>
  </main>;
}
