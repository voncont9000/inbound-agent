import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useLocation, useNavigate } from "react-router-dom";
import { api, unwrap, type Schemas } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { ErrorState, LoadingState } from "../components/States";
import { DraftSectionCard, type DraftSectionStatus } from "../components/setup/DraftSectionCard";
import { DraftSectionEditor, DraftSectionPreview, type DraftSectionName } from "../components/setup/DraftSectionEditor";
import { TemplatePicker } from "../components/setup/TemplatePicker";
import { settingsTabPath } from "../lib/settingsTabs";
import { businessNameFromWebsite, normalizeWebsiteUrl } from "../lib/url";

type SetupStep = "form" | "waiting" | "review";
type DraftJobResult = { draft: Schemas["SetupDraft"]; warnings?: string[] };

const sectionTitles: Record<DraftSectionName, string> = {
  company_info: "Company info",
  faqs: "FAQs",
  office_hours: "Opening hours",
  price_list: "Price list",
  services: "Suggested services",
};
const sectionOrder = Object.keys(sectionTitles) as DraftSectionName[];
const initialStatuses: Record<DraftSectionName, DraftSectionStatus> = {
  company_info: "pending",
  faqs: "pending",
  office_hours: "pending",
  price_list: "pending",
  services: "pending",
};

export function AssistantSetupPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { tenant, loading: authLoading } = useAuth();
  const queryWebsite = new URLSearchParams(location.search).get("website") ?? "";
  const templatesQuery = useQuery({ queryKey: ["templates"], queryFn: () => unwrap(api.GET("/templates")) });
  const [businessName, setBusinessName] = useState(() => businessNameFromWebsite(queryWebsite));
  const [businessNameEdited, setBusinessNameEdited] = useState(false);
  const [assistantName, setAssistantName] = useState("");
  const [websiteUrl, setWebsiteUrl] = useState(queryWebsite);
  const [selectedTemplate, setSelectedTemplate] = useState("");
  const [step, setStep] = useState<SetupStep>("form");
  const [jobId, setJobId] = useState("");
  const autoReadStarted = useRef(false);
  const [draft, setDraft] = useState<Schemas["SetupDraft"] | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [statuses, setStatuses] = useState(initialStatuses);
  const [editingSection, setEditingSection] = useState<DraftSectionName | null>(null);
  const [editingValue, setEditingValue] = useState<unknown>(null);

  const complete = useMutation({
    mutationFn: (body: Schemas["AssistantSetupComplete"]) => unwrap(api.POST("/assistant-setup/complete", { body })),
    onSuccess: (assistant) => navigate(settingsTabPath(assistant.id)),
  });
  const startDraft = useMutation({
    mutationFn: (body: Schemas["AssistantSetupDraftCreate"]) => unwrap(api.POST("/assistant-setup/drafts", { body })),
    onSuccess: (job) => {
      setJobId(job.id);
      setStep("waiting");
    },
    onError: () => setStep("waiting"),
  });
  const startDraftMutation = startDraft.mutate;
  const readWebsite = useCallback((website: string) => {
    const normalizedWebsiteUrl = normalizeWebsiteUrl(website);
    const name = businessName.trim() || tenant?.name?.trim() || businessNameFromWebsite(website);
    if (!normalizedWebsiteUrl || !selectedTemplate || !name) return;
    startDraftMutation({
      business_name: name,
      website_url: normalizedWebsiteUrl,
      template: selectedTemplate,
    });
  }, [businessName, selectedTemplate, startDraftMutation, tenant?.name]);
  const jobQuery = useQuery({
    queryKey: ["assistant-setup-draft", jobId],
    queryFn: () => unwrap(api.GET("/jobs/{job_id}", { params: { path: { job_id: jobId } } })),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 1500 : false;
    },
  });
  const result = jobQuery.data?.status === "succeeded"
    ? jobQuery.data.result as DraftJobResult | null
    : null;

  useEffect(() => {
    if (!result?.draft || step !== "waiting") return;
    setDraft(result.draft);
    setWarnings(result.warnings ?? []);
    setStatuses({ ...initialStatuses });
    setStep("review");
  }, [result, step]);

  useEffect(() => {
    if (!selectedTemplate && templatesQuery.data?.length) {
      setSelectedTemplate(
        templatesQuery.data.find(({ name }) => name === "blank")?.name ??
          templatesQuery.data[0].name,
      );
    }
  }, [selectedTemplate, templatesQuery.data]);

  useEffect(() => {
    const tenantName = tenant?.name?.trim();
    if (!tenantName || businessNameEdited) return;
    setBusinessName(tenantName);
  }, [businessNameEdited, tenant?.name]);

  useEffect(() => {
    if (authLoading || !queryWebsite || !selectedTemplate || autoReadStarted.current) return;
    const tenantName = tenant?.name?.trim();
    if (tenantName && !businessNameEdited && businessName.trim() !== tenantName) return;
    autoReadStarted.current = true;
    readWebsite(queryWebsite);
  }, [authLoading, businessName, businessNameEdited, queryWebsite, readWebsite, selectedTemplate, tenant?.name]);

  function makeCompletion(accepted: Schemas["SetupDraft"]) {
    const name = assistantName.trim() || businessName.trim();
    const normalizedWebsiteUrl = normalizeWebsiteUrl(websiteUrl);
    complete.mutate({
      name,
      business_name: businessName.trim(),
      template: selectedTemplate,
      ...(normalizedWebsiteUrl ? { website_url: normalizedWebsiteUrl } : {}),
      accepted,
      use_sample_content: false,
    });
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedTemplate) return;
    const normalizedWebsiteUrl = normalizeWebsiteUrl(websiteUrl);
    if (normalizedWebsiteUrl) {
      readWebsite(websiteUrl);
    } else {
      makeCompletion({});
    }
  }

  const currentJobStatus = jobQuery.data?.status;
  const draftFailed = Boolean(
    startDraft.error ||
    jobQuery.error ||
    currentJobStatus === "failed",
  );
  const draftFailureMessage = startDraft.error instanceof Error
    ? startDraft.error.message
    : jobQuery.error instanceof Error
      ? jobQuery.error.message
      : jobQuery.data?.error ?? "Website drafting failed.";
  const sections = draft
    ? sectionOrder.filter((section) => draft[section] !== null && draft[section] !== undefined)
    : [];
  const emptyDraft = draft !== null && sections.length === 0;
  const pendingCount = sections.filter((section) => statuses[section] === "pending").length;

  function setDraftSection(section: DraftSectionName, value: unknown) {
    setDraft((current) => current ? { ...current, [section]: value } : current);
  }

  function acceptSections() {
    if (!draft) return;
    const accepted: Schemas["SetupDraft"] = {};
    for (const section of sectionOrder) {
      if (statuses[section] !== "accepted") continue;
      const value = draft[section];
      if (value !== null && value !== undefined) accepted[section] = value as never;
    }
    makeCompletion(accepted);
  }

  function continueWithDefaults() {
    makeCompletion({});
  }

  function createDemo(template: Schemas["TemplateResponse"]) {
    if (!template.sample_business_name) return;
    complete.mutate({
      name: `${template.label} demo`,
      business_name: template.sample_business_name,
      template: template.name,
      accepted: {},
      use_sample_content: true,
    });
  }

  return <main className="page page-narrow page-template-setup" id="main-content">
    <p className="eyebrow">Assistant setup</p>
    <h1>Create an assistant</h1>
    {step === "form" && <section className="panel">
      <h2>Business details</h2>
      {templatesQuery.isLoading && <LoadingState label="Loading templates…" />}
      {templatesQuery.error && <ErrorState message="Templates could not be loaded." />}
      <form className="stack" onSubmit={submit}>
        <div className="field">
          <label htmlFor="setup-business-name">Business name</label>
          <input id="setup-business-name" required value={businessName} onChange={(event) => {
            setBusinessName(event.target.value);
            setBusinessNameEdited(true);
          }} />
        </div>
        <div className="field">
          <label htmlFor="setup-assistant-name">Assistant name</label>
          <input id="setup-assistant-name" value={assistantName} onChange={(event) => setAssistantName(event.target.value)} />
          <span className="help">If left blank, the business name will be used.</span>
        </div>
        <div className="field">
          <label htmlFor="setup-website-url">Website URL (optional)</label>
          <input id="setup-website-url" type="text" inputMode="url" autoComplete="url" placeholder="example.co.uk" value={websiteUrl} onChange={(event) => setWebsiteUrl(event.target.value)} />
        </div>
        <TemplatePicker
          templates={templatesQuery.data ?? []}
          selectedTemplate={selectedTemplate}
          onSelect={setSelectedTemplate}
          onCreateDemo={createDemo}
          demoPending={complete.isPending}
        />
        {complete.error && <ErrorState message={complete.error instanceof Error ? complete.error.message : "Assistant creation failed."} />}
        <button className="button-primary" type="submit" disabled={templatesQuery.isLoading || !selectedTemplate || startDraft.isPending || complete.isPending}>
          {complete.isPending ? "Creating assistant…" : websiteUrl.trim() ? "Read website" : "Create assistant"}
        </button>
      </form>
    </section>}

    {step === "waiting" && !draftFailed && <section className="panel" aria-live="polite">
      <LoadingState label="Reading your website…" />
      {currentJobStatus === "running" && <p className="muted">This may take a moment.</p>}
    </section>}
    {step === "waiting" && draftFailed && <section className="panel">
      <ErrorState message={draftFailureMessage} />
      {complete.error && <ErrorState message={complete.error instanceof Error ? complete.error.message : "Assistant creation failed."} />}
      <button className="button-primary" type="button" onClick={continueWithDefaults} disabled={complete.isPending}>
        Continue with template defaults
      </button>
    </section>}

    {step === "review" && draft && <section className="stack" aria-labelledby="review-heading">
      <div>
        <p className="eyebrow">Review website draft</p>
        <h2 id="review-heading">Choose what to include</h2>
        <p className="muted">Only accepted sections will be added to the new assistant.</p>
      </div>
      {warnings.length > 0 && <section className="panel" aria-label="Draft warnings">
        <h2>Warnings</h2>
        <ul>{warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>
      </section>}
      {emptyDraft ? <section className="panel">
        <p>Nothing could be drafted from the website.</p>
        {complete.error && <ErrorState message={complete.error instanceof Error ? complete.error.message : "Assistant creation failed."} />}
        <button className="button-primary" type="button" onClick={continueWithDefaults} disabled={complete.isPending}>
          Create with template defaults
        </button>
      </section> : <>
        {sections.map((section) => {
          const value = draft[section];
          const editing = editingSection === section;
          return <DraftSectionCard
            key={section}
            title={sectionTitles[section]}
            status={statuses[section]}
            editing={editing}
            onAccept={() => setStatuses((current) => ({ ...current, [section]: "accepted" }))}
            onEdit={() => {
              setEditingSection(section);
              setEditingValue(structuredClone(value));
            }}
            onSave={() => {
              setDraftSection(section, editingValue);
              setStatuses((current) => ({ ...current, [section]: "accepted" }));
              setEditingSection(null);
              setEditingValue(null);
            }}
            onCancel={() => {
              setEditingSection(null);
              setEditingValue(null);
            }}
            onDiscard={() => setStatuses((current) => ({ ...current, [section]: "discarded" }))}
            onUndo={() => setStatuses((current) => ({ ...current, [section]: "pending" }))}
          >
            {editing
              ? <DraftSectionEditor section={section} value={editingValue} onChange={setEditingValue} />
              : <DraftSectionPreview section={section} value={value} />}
          </DraftSectionCard>;
        })}
        <section className="panel stack">
          <p className="muted">{pendingCount} {pendingCount === 1 ? "section" : "sections"} not reviewed won't be used.</p>
          {complete.error && <ErrorState message={complete.error instanceof Error ? complete.error.message : "Assistant creation failed."} />}
          <button className="button-primary" type="button" onClick={acceptSections} disabled={complete.isPending}>
            {complete.isPending ? "Creating assistant…" : "Create assistant"}
          </button>
        </section>
      </>}
    </section>}
  </main>;
}
