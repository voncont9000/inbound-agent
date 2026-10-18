import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, unwrap, type Schemas } from "../api/client";
import { ErrorState, LoadingState } from "../components/States";
import { DraftSectionCard, type DraftSectionStatus } from "../components/setup/DraftSectionCard";
import { DraftSectionEditor, DraftSectionPreview, type DraftSectionName } from "../components/setup/DraftSectionEditor";

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
  const navigate = useNavigate();
  const templatesQuery = useQuery({ queryKey: ["templates"], queryFn: () => unwrap(api.GET("/templates")) });
  const [businessName, setBusinessName] = useState("");
  const [assistantName, setAssistantName] = useState("");
  const [websiteUrl, setWebsiteUrl] = useState("");
  const [selectedTemplate, setSelectedTemplate] = useState("");
  const [step, setStep] = useState<SetupStep>("form");
  const [jobId, setJobId] = useState("");
  const [draft, setDraft] = useState<Schemas["SetupDraft"] | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [statuses, setStatuses] = useState(initialStatuses);
  const [editingSection, setEditingSection] = useState<DraftSectionName | null>(null);
  const [editingValue, setEditingValue] = useState<unknown>(null);

  const complete = useMutation({
    mutationFn: (body: Schemas["AssistantSetupComplete"]) => unwrap(api.POST("/assistant-setup/complete", { body })),
    onSuccess: (assistant) => navigate(`/assistants/${assistant.id}/settings`),
  });
  const startDraft = useMutation({
    mutationFn: (body: Schemas["AssistantSetupDraftCreate"]) => unwrap(api.POST("/assistant-setup/drafts", { body })),
    onSuccess: (job) => {
      setJobId(job.id);
      setStep("waiting");
    },
    onError: () => setStep("waiting"),
  });
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
    if (!selectedTemplate && templatesQuery.data?.length) setSelectedTemplate(templatesQuery.data[0].name);
  }, [selectedTemplate, templatesQuery.data]);

  function makeCompletion(accepted: Schemas["SetupDraft"]) {
    const name = assistantName.trim() || businessName.trim();
    complete.mutate({
      name,
      business_name: businessName.trim(),
      template: selectedTemplate,
      ...(websiteUrl.trim() ? { website_url: websiteUrl.trim() } : {}),
      accepted,
    });
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedTemplate) return;
    if (websiteUrl.trim()) {
      startDraft.mutate({
        business_name: businessName.trim(),
        website_url: websiteUrl.trim(),
        template: selectedTemplate,
      });
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

  return <main className="page page-narrow" id="main-content">
    <p className="eyebrow">Assistant setup</p>
    <h1>Set up from your website</h1>
    {step === "form" && <section className="panel">
      <h2>Business details</h2>
      {templatesQuery.isLoading && <LoadingState label="Loading templates…" />}
      {templatesQuery.error && <ErrorState message="Templates could not be loaded." />}
      <form className="stack" onSubmit={submit}>
        <div className="field">
          <label htmlFor="setup-business-name">Business name</label>
          <input id="setup-business-name" required value={businessName} onChange={(event) => setBusinessName(event.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="setup-assistant-name">Assistant name</label>
          <input id="setup-assistant-name" value={assistantName} onChange={(event) => setAssistantName(event.target.value)} />
          <span className="help">If left blank, the business name will be used.</span>
        </div>
        <div className="field">
          <label htmlFor="setup-website-url">Website URL (optional)</label>
          <input id="setup-website-url" type="url" value={websiteUrl} onChange={(event) => setWebsiteUrl(event.target.value)} />
        </div>
        <fieldset className="stack">
          <legend>Start from template</legend>
          {(templatesQuery.data ?? []).map((template) => <label key={template.name} className="radio-row">
            <input type="radio" name="setup-template" value={template.name} checked={selectedTemplate === template.name} onChange={() => setSelectedTemplate(template.name)} />
            <span><strong>{template.name.replaceAll("_", " ")}</strong><br /><span className="muted">{Object.values(template.config.services).map((service) => service.label).join(", ") || "Blank configuration"}</span></span>
          </label>)}
        </fieldset>
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
