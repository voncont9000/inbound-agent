import { http, HttpResponse } from "msw";
import type { Schemas } from "../api/client";
import type { paths } from "../api/schema";
import { validateAssistantConfig, type Locale } from "../lib/config";
import { authUser, emailActionTemplates, setupDraftFixture, templates, templateSamples, testCallResponse, voiceCatalogueFor } from "./fixtures";
import { mockDashboardSummary, type DashboardMockQuery } from "./dashboard";
import { mockDb, selectedTenant, selectedTenantId } from "./db";
import type { TenantFixture } from "./fixtures";
import assistantConfigSchema from "./assistant-config.schema.json";
import type { AssistantConfig } from "../lib/config";

const isoNow = () => new Date().toISOString();
const requestJson = async <T>(request: Request) => (await request.json()) as T;
type ApiErrorResponse = { detail: string } | Schemas["HTTPValidationError"];
type ApiResponse<T> = T | ApiErrorResponse;
type KnowledgeSourceJsonBody = paths["/assistants/{assistant_id}/knowledge/sources"]["post"]["requestBody"]["content"]["application/json"];
type KnowledgeSourceMultipartBody = paths["/assistants/{assistant_id}/knowledge/sources"]["post"]["requestBody"]["content"]["multipart/form-data"];
type KnowledgeSourceRequestBody = KnowledgeSourceJsonBody | KnowledgeSourceMultipartBody;

function findAssistant(tenant: TenantFixture, id: string) {
  return tenant.assistants.find((assistant) => assistant.id === id);
}

function assistantWithConversationCount(tenant: TenantFixture, assistant: Schemas["AssistantResponse"]) {
  return {
    ...assistant,
    conversation_count: tenant.conversations.filter(
      (conversation) => conversation.assistant_id === assistant.id,
    ).length,
  };
}

function removeConversation(tenant: TenantFixture, id: string) {
  const index = tenant.conversations.findIndex((conversation) => conversation.id === id);
  if (index === -1) return false;
  tenant.conversations.splice(index, 1);
  tenant.contacts.forEach((contact) => {
    contact.conversations = contact.conversations.filter((conversation) => conversation.id !== id);
  });
  tenant.gaps = tenant.gaps.filter((gap) => gap.conversation_id !== id);
  tenant.tasks = tenant.tasks.map((task) => (
    task.conversation_id === id ? { ...task, conversation_id: null } : task
  ));
  return true;
}

function toConversationSummary(detail: Schemas["ConversationDetailResponse"]): Schemas["ConversationSummary"] {
  return {
    id: detail.id,
    assistant_id: detail.assistant_id,
    person_id: detail.person_id,
    external_ref: detail.external_ref,
    channel: detail.channel,
    status: detail.status,
    summary: detail.summary,
    started_at: detail.started_at,
    ended_at: detail.ended_at,
    end_reason: detail.end_reason,
    needs_review: detail.needs_review,
    test_call: detail.test_call,
    contact_name: detail.contact_name,
  };
}

function makeSilentWav() {
  const samples = 800;
  const bytes = new ArrayBuffer(44 + samples * 2);
  const view = new DataView(bytes);
  const writeText = (offset: number, text: string) => [...text].forEach((character, index) => view.setUint8(offset + index, character.charCodeAt(0)));
  writeText(0, "RIFF");
  view.setUint32(4, bytes.byteLength - 8, true);
  writeText(8, "WAVE");
  writeText(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, 8000, true);
  view.setUint32(28, 16000, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeText(36, "data");
  view.setUint32(40, samples * 2, true);
  return new Uint8Array(bytes);
}

const silentWav = makeSilentWav();

function toContactSummary(detail: Schemas["ContactDetailResponse"]): Schemas["ContactSummary"] {
  return {
    id: detail.id,
    display_name: detail.display_name,
    created_at: detail.created_at,
    updated_at: detail.updated_at,
    merged_into_id: detail.merged_into_id,
  };
}

export const handlers = [
  http.get<never, never, Schemas["DashboardSummary"]>("*/api/dashboard/summary", ({ request }) => {
    const query = new URL(request.url).searchParams;
    const tenant = selectedTenant(request);
    const assistantId = query.get("assistant_id");
    const summary = mockDashboardSummary({
      range: (query.get("range") ?? "7d") as DashboardMockQuery["range"],
      start: query.get("start"),
      end: query.get("end"),
      timezone: query.get("timezone") ?? "UTC",
      assistant_id: assistantId,
      include_test: query.get("include_test") === "true",
    }, mockDb.tenants.indexOf(tenant) + 1, assistantId && !findAssistant(tenant, assistantId) ? 0 : 1);
    return HttpResponse.json<Schemas["DashboardSummary"]>(summary);
  }),
  http.get<never, never, Schemas["VoiceCatalogueResponse"]>(
    "*/api/voices",
    ({ request }) => {
      const locale = (new URL(request.url).searchParams.get("locale") ??
        "en-GB") as Locale;
      return HttpResponse.json(voiceCatalogueFor(locale));
    },
  ),
  http.get<never, never, Schemas["PostActionVariable"][]>("*/api/post-actions/variables", () =>
    HttpResponse.json<Schemas["PostActionVariable"][]>([
      { name: "contact.name", description: "The customer's name." },
      { name: "contact.first_name", description: "The first part of the customer's name, when known." },
      { name: "channel", description: "The conversation channel." },
      { name: "summary", description: "Conversation summary." },
      { name: "customer_recap", description: "A customer-facing recap of the conversation." },
      { name: "next_step", description: "The title of the next open callback or follow-up task." },
      { name: "started_at", description: "The conversation start time in the assistant's timezone." },
    ]),
  ),
  http.get<never, never, Schemas["EmailActionTemplateResponse"][]>(
    "*/api/post-actions/email-templates",
    () => HttpResponse.json(emailActionTemplates),
  ),
  http.post<{ id: string }, Schemas["EmailPreviewRequest"], ApiResponse<Schemas["EmailPreviewResponse"]>>(
    "*/api/assistants/:id/post-actions/email-preview",
    async ({ request }) => {
      const body = await requestJson<Schemas["EmailPreviewRequest"]>(request);
      const context: Record<string, string> = {
        business_name: "Brightside Plumbing & Heating",
        "contact.name": "Alex Morgan",
        "contact.first_name": "Alex",
        "contact.email": "alex@example.com",
        "contact.phone": "+44 20 7946 0000",
        customer_recap: "You asked us to arrange a call about your enquiry.",
        next_step: "Call back",
        summary: "Alex Morgan asked for a call back about an enquiry.",
        channel: "phone",
        intake_table: "Name: Alex Morgan\nEmail: alex@example.com",
        "conversation.link": "https://app.example.test/conversations/preview",
        urgency_reasons: "",
      };
      const render = (template: string) => template.replace(
        /\{\{\s*([A-Za-z0-9_.]+)(?:\|([^{}|]*))?\s*\}\}/g,
        (_placeholder, name: string, fallback: string | undefined) =>
          context[name]?.trim() || fallback?.trim() || "",
      );
      const text = render(body.body_template);
      const escapeHtml = (value: string) => value
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll("\"", "&quot;");
      return HttpResponse.json<Schemas["EmailPreviewResponse"]>({
        subject: render(body.subject_template),
        text,
        html: `<html><body><h1>${escapeHtml(context.business_name)}</h1><p>${escapeHtml(text).replaceAll("\n", "<br>")}</p></body></html>`,
      });
    },
  ),
  http.post<{ id: string }, Schemas["PostActionTestRequest"], ApiResponse<Schemas["PostActionTestResponse"]>>("*/api/assistants/:id/post-actions/test", async ({ request, params }) => {
    const body = await requestJson<Schemas["PostActionTestRequest"]>(request);
    const action = body.action;
    const conversation = selectedTenant(request).conversations.find(({ assistant_id }) => assistant_id === params.id);
    if (!conversation) return HttpResponse.json<ApiErrorResponse>({ detail: "Conversation not found" }, { status: 404 });
    const rendered = action.kind === "email_customer" || action.kind === "notify_staff"
      ? { to: action.kind === "notify_staff" ? action.to : ["jamie.morgan@example.com"], subject: action.subject_template.replace("{{business_name}}", "Brightside Plumbing & Heating"), body: action.body_template.replace("{{summary}}", conversation.summary ?? "") }
      : action.kind === "sheets_row"
        ? { spreadsheet_id: action.spreadsheet_id, sheet_name: action.sheet_name, headers: action.columns.map(({ header }) => header), row: action.columns.map(({ value_template }) => value_template) }
        : { url: action.url, payload: { event_id: "dry-run", action_key: action.key, events: ["conversation_completed"], conversation_id: conversation.id, context: {} } };
    return HttpResponse.json<Schemas["PostActionTestResponse"]>({
      conversation_id: conversation.id,
      events: ["conversation_completed"],
      matched_events: ["conversation_completed"],
      would_skip: null,
      rendered,
    });
  }),
  http.get<{ id: string }, never, Schemas["WebhookSecretListResponse"]>("*/api/assistants/:id/post-actions/webhook-secrets", ({ params }) =>
    HttpResponse.json<Schemas["WebhookSecretListResponse"]>({ items: mockDb.postActionSecrets[String(params.id)] ?? [] }),
  ),
  http.post<{ id: string; action_key: string }, never, ApiResponse<Schemas["WebhookSecretCreateResponse"]>>("*/api/assistants/:id/post-actions/:action_key/webhook-secret", ({ params }) => {
    const secret = { action_key: String(params.action_key), secret: "mock-secret-value", created_at: isoNow() };
    const current = mockDb.postActionSecrets[String(params.id)] ?? [];
    mockDb.postActionSecrets[String(params.id)] = [...current.filter(({ action_key }) => action_key !== secret.action_key), { action_key: secret.action_key, created_at: secret.created_at }];
    return HttpResponse.json<Schemas["WebhookSecretCreateResponse"]>(secret);
  }),
  http.get<never, never, { mode: "off" | "fake" | "google" }>(
    "*/api/channels/gmail/mode",
    () => HttpResponse.json<{ mode: "off" | "fake" | "google" }>({ mode: "fake" }),
  ),
  http.get<never, never, { mode: "off" | "fake" | "google" }>(
    "*/api/calendar/mode",
    () => HttpResponse.json<{ mode: "off" | "fake" | "google" }>({ mode: "fake" }),
  ),
  http.post<{ id: string }, Schemas["GmailOAuthStartRequest"], { url: string }>(
    "*/api/assistants/:id/channels/gmail/oauth-start",
    ({ params }) =>
      HttpResponse.json<{ url: string }>({
        url: `https://accounts.google.com/o/oauth2/v2/auth?assistant=${String(params.id)}`,
      }),
  ),
  http.get<never, never, Record<string, unknown>>("*/api/assistant-config/schema", () =>
    HttpResponse.json<Record<string, unknown>>(assistantConfigSchema),
  ),
  http.get<never, never, Schemas["TemplateResponse"][]>("*/api/templates", () => HttpResponse.json<Schemas["TemplateResponse"][]>(templates)),
  http.get<never, never, Schemas["CustomTemplateOptions"]>("*/api/templates/custom-options", () => HttpResponse.json<Schemas["CustomTemplateOptions"]>({
    sectors: ["Legal services", "Restaurants and hospitality", "Automotive and garages", "Other"],
    use_cases: ["Answer enquiries and take messages", "Take bookings and reservations", "Qualify new leads"],
  })),
  http.post<never, Schemas["CustomTemplateRequest"], Schemas["CustomTemplateResponse"]>("*/api/templates/custom-draft", async ({ request }) => {
    const body = await requestJson<Schemas["CustomTemplateRequest"]>(request);
    const blank = templates.find(({ name }) => name === "blank") ?? templates[0];
    const config = structuredClone(blank.config) as AssistantConfig;
    if (body.language) config.language = body.language;
    config.greeting = `Hello, you are speaking with the AI assistant for ${config.business_name}. How can I help?`;
    config.policies = [`Follow the ${body.sector.toLocaleLowerCase()} rules for ${body.use_case.toLocaleLowerCase()}.`];
    config.services = {
      main_request: {
        label: body.use_case,
        fields: [
          { name: "caller_name", label: "Name", type: "string", required: false, critical: true, description: "" },
          { name: "phone", label: "Phone number", type: "phone", required: false, critical: true, description: "" },
        ],
      },
    };
    return HttpResponse.json<Schemas["CustomTemplateResponse"]>({
      label: `${body.sector} ${body.use_case.toLocaleLowerCase()}`,
      description: `Handles ${body.use_case.toLocaleLowerCase()} for a ${body.sector.toLocaleLowerCase()} business.`,
      sets_up: [`${body.use_case} intake`, `${body.sector} policies`],
      config,
      warnings: [],
    });
  }),
  http.post<never, Schemas["AssistantSetupDraftCreate"], ApiResponse<Schemas["JobResponse"]>>("*/api/assistant-setup/drafts", async ({ request }) => {
    const body = await requestJson<Schemas["AssistantSetupDraftCreate"]>(request);
    mockDb.setupDraftRequests.push(body);
    const id = crypto.randomUUID();
    const createdAt = isoNow();
    const job: Schemas["JobResponse"] = {
      id,
      kind: "assistant_setup_draft",
      status: "queued",
      payload: body,
      result: null,
      error: null,
      attempts: 0,
      run_after: createdAt,
      created_at: createdAt,
    };
    mockDb.jobs[id] = { tenantId: selectedTenantId(request), assistantId: "", polls: 0, job };
    return HttpResponse.json<Schemas["JobResponse"]>(job, { status: 202 });
  }),
  http.post<never, Schemas["AssistantSetupComplete"], ApiResponse<Schemas["AssistantResponse"]>>("*/api/assistant-setup/complete", async ({ request }) => {
    const body = await requestJson<Schemas["AssistantSetupComplete"]>(request);
    mockDb.setupCompletions.push(structuredClone(body));
    const tenant = selectedTenant(request);
    const selectedTemplate = templates.find(({ name }) => name === body.template);
    if (!selectedTemplate) return HttpResponse.json<ApiErrorResponse>({ detail: "Unknown template" }, { status: 422 });
    const sample = body.use_sample_content ? templateSamples[body.template] : undefined;
    if (body.use_sample_content && !sample) {
      return HttpResponse.json<ApiErrorResponse>({ detail: "Template has no sample content" }, { status: 422 });
    }
    const config = structuredClone(selectedTemplate.config) as AssistantConfig;
    config.business_name = body.business_name;
    const accepted = body.accepted;
    if (accepted?.office_hours) config.office_hours = structuredClone(accepted.office_hours);
    const priceList = accepted?.price_list ?? sample?.price_list;
    if (priceList) config.price_list = structuredClone(priceList);
    if (accepted?.services) config.services = structuredClone(accepted.services);
    const faqs = accepted?.faqs ?? sample?.faqs;
    if (faqs) {
      const acceptedQuestions = new Set(faqs.map(({ question }) => question.trim().toLocaleLowerCase()));
      const knowledge = config.knowledge ?? { faqs: [], max_digest_chars: 24000 };
      config.knowledge = {
        ...knowledge,
        faqs: [
          ...(knowledge.faqs ?? []).filter(({ question }) => !acceptedQuestions.has(question.trim().toLocaleLowerCase())),
          ...structuredClone(faqs),
        ],
      };
    }
    const companyInfo = accepted?.company_info ?? sample?.company_info;
    const baseAssistant = tenant.assistants[0];
    const baseState = tenant.assistantData[baseAssistant.id];
    const id = crypto.randomUUID();
    const createdAt = isoNow();
    const assistant: Schemas["AssistantResponse"] = {
      id,
      name: body.name,
      template: body.template,
      config,
      version: 1,
      created_at: createdAt,
      updated_at: createdAt,
      conversation_count: 0,
    };
    tenant.assistants.push(assistant);
    tenant.assistantData[id] = {
      versions: [{ id: `${id}-version-1`, version: 1, config, created_by: authUser.id, created_at: createdAt }],
      sources: body.website_url
        ? [{ id: `${id}-source-1`, assistant_id: id, kind: "url", uri: body.website_url, status: "pending", manifest: {}, fetched_at: null, created_at: createdAt }]
        : [],
      digest: {
        ...baseState.digest,
        id: `${id}-digest`,
        assistant_id: id,
        content: companyInfo ?? "",
        created_at: createdAt,
      },
    };
    return HttpResponse.json<Schemas["AssistantResponse"]>(assistant, { status: 201 });
  }),
  http.post<never, Schemas["LoginRequest"], ApiResponse<Schemas["AuthResponse"]>>("*/api/auth/login", async ({ request }) => {
    const body = await requestJson<Schemas["LoginRequest"]>(request);
    if (body.password === "wrong") return HttpResponse.json<ApiErrorResponse>({ detail: "Invalid email or password" }, { status: 401 });
    mockDb.loggedIn = true;
    mockDb.user = { ...authUser, email: body.email };
    if (typeof localStorage !== "undefined") localStorage.setItem("inbound.mock.session", "1");
    return HttpResponse.json<Schemas["AuthResponse"]>({ user: mockDb.user, tenants: mockDb.memberships, access_token: "mock-token" });
  }),
  http.post<never, Schemas["SignupRequest"], ApiResponse<Schemas["AuthResponse"]>>("*/api/auth/signup", async ({ request }) => {
    const body = await requestJson<Schemas["SignupRequest"]>(request);
    if (body.password.length < 12 || !body.email.includes("@")) {
      return HttpResponse.json<Schemas["HTTPValidationError"]>({ detail: [{ loc: ["body", "password"], msg: "Password must contain at least 12 characters.", type: "value_error" }] }, { status: 422 });
    }
    const templateTenant = mockDb.tenants[0];
    const tenantId = `10000000-0000-4000-8000-${String(mockDb.memberships.length + 1).padStart(12, "0")}`;
    const assistantId = `20000000-0000-4000-8000-${String(mockDb.tenants.length + 1).padStart(12, "0")}`;
    const tenant = { id: tenantId, name: body.tenant_name, role: "owner" as const };
    const templateAssistant = templateTenant.assistants[0];
    const assistant: Schemas["AssistantResponse"] = {
      ...structuredClone(templateAssistant),
      id: assistantId,
      name: `${body.tenant_name} assistant`,
      template: "blank",
      config: { ...structuredClone(templateAssistant.config), business_name: body.tenant_name, services: {}, urgency_rules: [], policies: [], price_list: [], contacts: {}, knowledge: { faqs: [], max_digest_chars: 24000 } },
    };
    const createdAt = isoNow();
    const fixture: TenantFixture = {
      ...structuredClone(templateTenant),
      tenant,
      assistants: [assistant],
      assistantData: {
        [assistantId]: {
          versions: [{ id: `${assistantId}-version-1`, version: 1, config: assistant.config, created_by: authUser.id, created_at: createdAt }],
          sources: [],
          digest: { ...templateTenant.assistantData[templateAssistant.id].digest, id: `${assistantId}-digest`, assistant_id: assistantId, content: "", created_at: createdAt },
        },
      },
      conversations: [],
      contacts: [],
      gaps: [],
      tasks: [],
    };
    mockDb.tenants.push(fixture);
    mockDb.memberships.push(tenant);
    mockDb.user = { id: authUser.id, email: body.email, display_name: body.display_name };
    mockDb.loggedIn = true;
    if (typeof localStorage !== "undefined") localStorage.setItem("inbound.mock.session", "1");
    return HttpResponse.json<Schemas["AuthResponse"]>({ user: mockDb.user, tenants: mockDb.memberships, access_token: "mock-token" }, { status: 201 });
  }),
  http.post<never, never, Schemas["LogoutResponse"]>("*/api/auth/logout", () => {
    mockDb.loggedIn = false;
    if (typeof localStorage !== "undefined") localStorage.removeItem("inbound.mock.session");
    return HttpResponse.json<Schemas["LogoutResponse"]>({ logged_out: true });
  }),
  http.get<never, never, ApiResponse<Schemas["MeResponse"]>>("*/api/me", ({ request }) => {
    if (!mockDb.loggedIn) return HttpResponse.json<ApiErrorResponse>({ detail: "Not authenticated" }, { status: 401 });
    return HttpResponse.json<Schemas["MeResponse"]>({ user: mockDb.user, tenant: mockDb.memberships.find(({ id }) => id === selectedTenantId(request)) ?? mockDb.memberships[0], memberships: mockDb.memberships });
  }),
  http.get<never, never, Schemas["AssistantListResponse"]>("*/api/assistants", ({ request }) => {
    const tenant = selectedTenant(request);
    return HttpResponse.json<Schemas["AssistantListResponse"]>({
      items: tenant.assistants.map((assistant) => assistantWithConversationCount(tenant, assistant)),
    });
  }),
  http.post<{ id: string }, never, Schemas["ServicesDraftResponse"]>("*/api/assistants/:id/services-draft", () => HttpResponse.json<Schemas["ServicesDraftResponse"]>({
    services: {
      table_reservation: { label: "Table reservation", fields: [
        { name: "name", label: "Name", type: "string", required: true, critical: true, description: "" },
        { name: "phone_number", label: "Phone number", type: "phone", required: true, critical: true, description: "" },
        { name: "party_size", label: "Party size", type: "integer", required: true, critical: false, description: "" },
      ] },
    },
    warnings: [],
  })),
  http.post<{ id: string }, Schemas["TranslateLinesRequest"], Schemas["TranslateLinesResponse"]>(
    "*/api/assistants/:id/translate-lines",
    () => HttpResponse.json<Schemas["TranslateLinesResponse"]>({
      greeting: "Ciao, parli con l’assistente AI di Brightside Plumbing & Heating.",
      call_settings: {
        max_call_message: "Abbiamo raggiunto il limite di durata della chiamata.",
        still_there_prompt: "È ancora in linea?",
        silence_goodbye_message: "Terminerò la chiamata. Grazie per aver chiamato.",
      },
      transfer_handoff_message: "Ora la trasferisco, resti in linea.",
      transfer_failed_message: "Mi dispiace, nessuno è disponibile al momento.",
    }),
  ),
  http.get<never, never, Schemas["ChannelConnectionsResponse"]>("*/api/channels", ({ request }) => {
    const tenant = selectedTenant(request);
    const ids = new Set(tenant.assistants.map(({ id }) => id));
    const items = [
      ...Object.entries(mockDb.channels)
        .filter(([assistantId]) => ids.has(assistantId))
        .flatMap(([, channels]) => channels),
      ...(mockDb.unassignedChannels[tenant.tenant.id] ?? []),
    ]
      .sort((a, b) => b.created_at.localeCompare(a.created_at));
    return HttpResponse.json<Schemas["ChannelConnectionsResponse"]>({ items });
  }),
  http.post<never, Schemas["WorkspaceChannelCreate"], ApiResponse<Schemas["ChannelConnectionResponse"]>>("*/api/channels", async ({ request }) => {
    const body = await requestJson<Schemas["WorkspaceChannelCreate"]>(request);
    const tenant = selectedTenant(request);
    if (!findAssistant(tenant, body.assistant_id)) return HttpResponse.json<ApiErrorResponse>({ detail: "Assistant not found" }, { status: 404 });
    const ids = new Set(tenant.assistants.map(({ id }) => id));
    const duplicate = [
      ...Object.entries(mockDb.channels).filter(([assistantId]) => ids.has(assistantId)).flatMap(([, channels]) => channels),
      ...(mockDb.unassignedChannels[tenant.tenant.id] ?? []),
    ].some((channel) => channel.kind === body.kind && channel.external_id === body.external_id);
    if (duplicate) return HttpResponse.json<ApiErrorResponse>({ detail: "This channel is already connected" }, { status: 409 });
    const channel: Schemas["ChannelConnectionResponse"] = {
      id: crypto.randomUUID(), assistant_id: body.assistant_id, kind: body.kind,
      external_id: body.external_id, status: "active", settings: body.settings ?? {},
      created_at: isoNow(), google_features: [],
    };
    mockDb.channels[body.assistant_id] = [...(mockDb.channels[body.assistant_id] ?? []), channel];
    return HttpResponse.json<Schemas["ChannelConnectionResponse"]>(channel, { status: 201 });
  }),
  http.patch<{ channel_id: string }, Schemas["ChannelConnectionUpdate"], ApiResponse<Schemas["ChannelConnectionResponse"]>>("*/api/channels/:channel_id", async ({ params, request }) => {
    const channelId = String(params.channel_id);
    const tenant = selectedTenant(request);
    const tenantAssistantIds = new Set(tenant.assistants.map(({ id }) => id));
    const currentAssistantId = Object.keys(mockDb.channels).find((assistantId) =>
      tenantAssistantIds.has(assistantId) && (mockDb.channels[assistantId] ?? []).some(({ id }) => id === channelId),
    );
    const unassigned = mockDb.unassignedChannels[tenant.tenant.id] ?? [];
    const isUnassigned = unassigned.some(({ id }) => id === channelId);
    if (!currentAssistantId && !isUnassigned) return HttpResponse.json<ApiErrorResponse>({ detail: "Channel connection not found" }, { status: 404 });
    const body = await requestJson<Schemas["ChannelConnectionUpdate"]>(request);
    const channel = currentAssistantId
      ? mockDb.channels[currentAssistantId].find(({ id }) => id === channelId)!
      : unassigned.find(({ id }) => id === channelId)!;
    if (body.assistant_id && !findAssistant(tenant, body.assistant_id)) {
      return HttpResponse.json<ApiErrorResponse>({ detail: "Assistant not found" }, { status: 404 });
    }
    if (body.include_rules !== undefined && channel.kind !== "gmail") {
      return HttpResponse.json<ApiErrorResponse>({ detail: "Include rules are only supported for Gmail" }, { status: 422 });
    }
    const assistantId = body.assistant_id ?? channel.assistant_id;
    const updated: Schemas["ChannelConnectionResponse"] = {
      ...channel,
      assistant_id: assistantId,
      settings: body.include_rules === undefined
        ? channel.settings
        : { ...channel.settings, include_rules: body.include_rules },
    };
    if (currentAssistantId) {
      mockDb.channels[currentAssistantId] = mockDb.channels[currentAssistantId].filter(({ id }) => id !== channelId);
    } else {
      mockDb.unassignedChannels[tenant.tenant.id] = unassigned.filter(({ id }) => id !== channelId);
    }
    if (assistantId) mockDb.channels[assistantId] = [...(mockDb.channels[assistantId] ?? []), updated];
    else mockDb.unassignedChannels[tenant.tenant.id] = [...(mockDb.unassignedChannels[tenant.tenant.id] ?? []), updated];
    return HttpResponse.json<Schemas["ChannelConnectionResponse"]>(updated);
  }),
  http.delete<{ channel_id: string }, never, ApiResponse<never>>("*/api/channels/:channel_id", ({ params, request }) => {
    const channelId = String(params.channel_id);
    const tenant = selectedTenant(request);
    const tenantAssistantIds = new Set(tenant.assistants.map(({ id }) => id));
    const assistantId = Object.keys(mockDb.channels).find((id) =>
      tenantAssistantIds.has(id) && (mockDb.channels[id] ?? []).some(({ id: rowId }) => rowId === channelId),
    );
    if (assistantId) {
      mockDb.channels[assistantId] = mockDb.channels[assistantId].filter(({ id }) => id !== channelId);
    } else {
      const unassigned = mockDb.unassignedChannels[tenant.tenant.id] ?? [];
      if (!unassigned.some(({ id }) => id === channelId)) {
        return HttpResponse.json<ApiErrorResponse>({ detail: "Channel connection not found" }, { status: 404 });
      }
      mockDb.unassignedChannels[tenant.tenant.id] = unassigned.filter(({ id }) => id !== channelId);
    }
    return new HttpResponse(null, { status: 204 });
  }),
  http.get<{ id: string }, never, Schemas["ChannelConnectionsResponse"]>("*/api/assistants/:id/channels", ({ params }) => HttpResponse.json<Schemas["ChannelConnectionsResponse"]>({ items: mockDb.channels[String(params.id)] ?? [] })),
  http.post<{ id: string }, Schemas["ChannelConnectionCreate"], ApiResponse<Schemas["ChannelConnectionResponse"]>>("*/api/assistants/:id/channels", async ({ params, request }) => {
    const assistantId = String(params.id);
    const tenant = selectedTenant(request);
    if (!findAssistant(tenant, assistantId)) return HttpResponse.json<ApiErrorResponse>({ detail: "Assistant not found" }, { status: 404 });
    const body = await requestJson<Schemas["ChannelConnectionCreate"]>(request);
    const connections = mockDb.channels[assistantId] ?? [];
    if (connections.some((channel) => channel.kind === body.kind && channel.external_id === body.external_id)) {
      return HttpResponse.json<ApiErrorResponse>({ detail: "This channel is already connected" }, { status: 409 });
    }
    const channel: Schemas["ChannelConnectionResponse"] = {
      id: crypto.randomUUID(),
      assistant_id: assistantId,
      kind: body.kind,
      external_id: body.external_id,
      status: "active",
      settings: body.settings ?? {},
      created_at: new Date().toISOString(),
      google_features: [],
    };
    mockDb.channels[assistantId] = [...connections, channel];
    return HttpResponse.json<Schemas["ChannelConnectionResponse"]>(channel, { status: 201 });
  }),
  http.patch<{ id: string; channel_id: string }, Schemas["ChannelConnectionUpdate"], ApiResponse<Schemas["ChannelConnectionResponse"]>>("*/api/assistants/:id/channels/:channel_id", async ({ params, request }) => {
    const assistantId = String(params.id);
    const channelId = String(params.channel_id);
    const tenant = selectedTenant(request);
    if (!findAssistant(tenant, assistantId)) return HttpResponse.json<ApiErrorResponse>({ detail: "Assistant not found" }, { status: 404 });
    const channel = (mockDb.channels[assistantId] ?? []).find(({ id }) => id === channelId);
    if (!channel) return HttpResponse.json<ApiErrorResponse>({ detail: "Channel connection not found" }, { status: 404 });
    if (channel.kind !== "gmail") return HttpResponse.json<ApiErrorResponse>({ detail: "Include rules are only supported for Gmail" }, { status: 422 });
    const body = await requestJson<Schemas["ChannelConnectionUpdate"]>(request);
    const updated = { ...channel, settings: { ...channel.settings, include_rules: body.include_rules ?? [] } };
    mockDb.channels[assistantId] = (mockDb.channels[assistantId] ?? []).map((item) => item.id === channelId ? updated : item);
    return HttpResponse.json<Schemas["ChannelConnectionResponse"]>(updated);
  }),
  http.delete<{ id: string; channel_id: string }, never, ApiResponse<never>>("*/api/assistants/:id/channels/:channel_id", ({ params, request }) => {
    const assistantId = String(params.id);
    const channelId = String(params.channel_id);
    if (!findAssistant(selectedTenant(request), assistantId)) return HttpResponse.json<ApiErrorResponse>({ detail: "Assistant not found" }, { status: 404 });
    const channels = mockDb.channels[assistantId] ?? [];
    if (!channels.some(({ id }) => id === channelId)) return HttpResponse.json<ApiErrorResponse>({ detail: "Channel connection not found" }, { status: 404 });
    mockDb.channels[assistantId] = channels.filter(({ id }) => id !== channelId);
    return new HttpResponse(null, { status: 204 });
  }),
  http.post<{ id: string }, never, ApiResponse<Schemas["TestCallResponse"]>>("*/api/assistants/:id/test-calls", ({ request, params }) => {
    return findAssistant(selectedTenant(request), String(params.id))
      ? HttpResponse.json<Schemas["TestCallResponse"]>(testCallResponse)
      : HttpResponse.json<ApiErrorResponse>({ detail: "Assistant not found" }, { status: 404 });
  }),
  http.post<never, Schemas["AssistantCreate"], ApiResponse<Schemas["AssistantResponse"]>>("*/api/assistants", async ({ request }) => {
    const body = await requestJson<Schemas["AssistantCreate"]>(request);
    const tenant = selectedTenant(request);
    const baseAssistant = tenant.assistants[0];
    const id = crypto.randomUUID();
    const createdAt = isoNow();
    const assistant: Schemas["AssistantResponse"] = {
      ...structuredClone(baseAssistant),
      id,
      name: body.name,
      template: body.template ?? null,
      config: structuredClone(body.config),
      version: 1,
      created_at: createdAt,
      updated_at: createdAt,
      conversation_count: 0,
    };
    const baseState = tenant.assistantData[baseAssistant.id];
    tenant.assistants.push(assistant);
    tenant.assistantData[id] = {
      versions: [{ id: `${id}-version-1`, version: 1, config: assistant.config, created_by: authUser.id, created_at: createdAt }],
      sources: [],
      digest: { ...baseState.digest, id: `${id}-digest`, assistant_id: id, content: "", created_at: createdAt },
    };
    return HttpResponse.json<Schemas["AssistantResponse"]>(assistant, { status: 201 });
  }),
  http.delete<{ id: string }>("*/api/assistants/:id", async ({ request, params }) => {
    const tenant = selectedTenant(request);
    const assistantId = String(params.id);
    const assistantIndex = tenant.assistants.findIndex(({ id }) => id === assistantId);
    if (assistantIndex === -1) {
      return HttpResponse.json<ApiErrorResponse>({ detail: "Assistant not found" }, { status: 404 });
    }
    const url = new URL(request.url);
    const deleteConversations = url.searchParams.get("delete_conversations") === "true";
    const conversations = tenant.conversations.filter(
      (conversation) => conversation.assistant_id === assistantId,
    );
    if (conversations.length && !deleteConversations) {
      return HttpResponse.json<ApiErrorResponse>({ detail: "Assistant has conversations" }, { status: 409 });
    }
    if (conversations.some(({ status }) => status === "active")) {
      return HttpResponse.json<ApiErrorResponse>({ detail: "Conversation is still in progress" }, { status: 409 });
    }
    conversations.forEach(({ id }) => removeConversation(tenant, id));
    tenant.assistants.splice(assistantIndex, 1);
    delete tenant.assistantData[assistantId];
    const channels = (mockDb.channels[assistantId] ?? []).map((channel) => ({
      ...channel,
      assistant_id: null,
    }));
    mockDb.unassignedChannels[tenant.tenant.id] = [
      ...(mockDb.unassignedChannels[tenant.tenant.id] ?? []),
      ...channels,
    ];
    mockDb.channels[assistantId] = [];
    return new HttpResponse(null, { status: 204 });
  }),
  http.get<{ id: string }, never, ApiResponse<Schemas["AssistantResponse"]>>("*/api/assistants/:id", ({ request, params }) => {
    const tenant = selectedTenant(request);
    const assistant = findAssistant(tenant, String(params.id));
    return assistant ? HttpResponse.json<Schemas["AssistantResponse"]>(assistantWithConversationCount(tenant, assistant)) : HttpResponse.json<ApiErrorResponse>({ detail: "Assistant not found" }, { status: 404 });
  }),
  http.put<{ id: string }, Schemas["AssistantUpdate"], ApiResponse<Schemas["AssistantResponse"]>>("*/api/assistants/:id", async ({ request, params }) => {
    const body = await requestJson<Schemas["AssistantUpdate"]>(request);
    const tenant = selectedTenant(request);
    const assistantIndex = tenant.assistants.findIndex(({ id }) => id === params.id);
    const currentAssistant = tenant.assistants[assistantIndex];
    const state = tenant.assistantData[String(params.id)];
    if (!currentAssistant || !state) return HttpResponse.json<ApiErrorResponse>({ detail: "Assistant not found" }, { status: 404 });
    const errors = validateAssistantConfig(body.config);
    if (Object.keys(errors).length) {
      return HttpResponse.json<Schemas["HTTPValidationError"]>({ detail: Object.entries(errors).map(([path, msg]) => ({ loc: ["body", "config", ...path.split(".").map((part) => /^\d+$/.test(part) ? Number(part) : part)], msg, type: "value_error" })) }, { status: 422 });
    }
    const updated = { ...currentAssistant, name: body.name ?? currentAssistant.name, template: body.template ?? currentAssistant.template, config: body.config, version: currentAssistant.version + 1, updated_at: isoNow() };
    tenant.assistants[assistantIndex] = updated;
    state.versions.push({ id: `${updated.id}-version-${updated.version}`, version: updated.version, config: structuredClone(updated.config), created_by: authUser.id, created_at: updated.updated_at });
    return HttpResponse.json<Schemas["AssistantResponse"]>(updated);
  }),
  http.get<{ id: string }, never, ApiResponse<Schemas["AssistantVersionsResponse"]>>("*/api/assistants/:id/versions", ({ request, params }) => {
    const tenant = selectedTenant(request);
    const state = tenant.assistantData[String(params.id)];
    return findAssistant(tenant, String(params.id)) && state
      ? HttpResponse.json<Schemas["AssistantVersionsResponse"]>({ items: state.versions })
      : HttpResponse.json<ApiErrorResponse>({ detail: "Assistant not found" }, { status: 404 });
  }),
  http.get<{ id: string }, never, ApiResponse<Schemas["KnowledgeSourcesResponse"]>>("*/api/assistants/:id/knowledge/sources", ({ request, params }) => {
    const tenant = selectedTenant(request);
    const state = tenant.assistantData[String(params.id)];
    return findAssistant(tenant, String(params.id)) && state
      ? HttpResponse.json<Schemas["KnowledgeSourcesResponse"]>({ items: state.sources })
      : HttpResponse.json<ApiErrorResponse>({ detail: "Assistant not found" }, { status: 404 });
  }),
  http.post<{ id: string }, KnowledgeSourceRequestBody, ApiResponse<Schemas["KnowledgeSourceResponse"]>>("*/api/assistants/:id/knowledge/sources", async ({ request, params }) => {
    const tenant = selectedTenant(request);
    const state = tenant.assistantData[String(params.id)];
    if (!findAssistant(tenant, String(params.id)) || !state) return HttpResponse.json<ApiErrorResponse>({ detail: "Assistant not found" }, { status: 404 });
    const form = request.headers.get("content-type")?.includes("multipart/form-data");
    let kind: "url" | "file" = "url";
    let uri = "";
    if (form) {
      const data = await request.formData();
      const file = data.get("file");
      if (file && typeof file !== "string") { kind = "file"; uri = file.name; }
    } else {
      const body = await requestJson<KnowledgeSourceJsonBody>(request);
      uri = body.uri;
    }
    const source: Schemas["KnowledgeSourceResponse"] = { id: crypto.randomUUID(), assistant_id: String(params.id), kind, uri, status: "pending", manifest: {}, fetched_at: null, created_at: isoNow() };
    state.sources.push(source);
    return HttpResponse.json<Schemas["KnowledgeSourceResponse"]>(source, { status: 201 });
  }),
  http.post<{ id: string }, never, ApiResponse<Schemas["JobResponse"]>>("*/api/assistants/:id/knowledge/import", ({ request, params }) => {
    const tenant = selectedTenant(request);
    if (!findAssistant(tenant, String(params.id)) || !tenant.assistantData[String(params.id)]) return HttpResponse.json<ApiErrorResponse>({ detail: "Assistant not found" }, { status: 404 });
    const id = crypto.randomUUID();
    const job: Schemas["JobResponse"] = { id, kind: "knowledge_import", status: "queued", payload: { assistant_id: params.id }, result: null, error: null, attempts: 0, run_after: isoNow(), created_at: isoNow() };
    mockDb.jobs[id] = { tenantId: selectedTenantId(request), assistantId: String(params.id), polls: 0, job };
    return HttpResponse.json<Schemas["JobResponse"]>(job, { status: 202 });
  }),
  http.get<{ id: string }, never, ApiResponse<Schemas["JobResponse"]>>("*/api/jobs/:id", ({ request, params }) => {
    const entry = mockDb.jobs[String(params.id)];
    if (!entry || entry.tenantId !== selectedTenantId(request)) return HttpResponse.json<ApiErrorResponse>({ detail: "Job not found" }, { status: 404 });
    if (entry.job.kind === "assistant_setup_draft") {
      entry.job = {
        ...entry.job,
        status: "succeeded",
        attempts: 1,
        result: {
          draft: structuredClone(setupDraftFixture),
          warnings: ["The website did not publish all requested details."],
          pages: [{ url: "https://harbordental.example/", title: "Harbor Dental Studio" }],
          model: "gemini-3.1-flash-lite",
          usage: { input_tokens: 1200, output_tokens: 300 },
          cost_usd: 0.00075,
        },
      };
      return HttpResponse.json<Schemas["JobResponse"]>(entry.job);
    }
    entry.polls += 1;
    const status = entry.polls < 2 ? "queued" : entry.polls < 3 ? "running" : "succeeded";
    entry.job = { ...entry.job, status, attempts: entry.polls, ...(status === "succeeded" ? { result: { imported: entry.assistantId } } : {}) };
    if (status === "succeeded") {
      const tenant = mockDb.tenants.find(({ tenant: { id } }) => id === entry.tenantId);
      const assistant = tenant?.assistants.find(({ id }) => id === entry.assistantId);
      const state = assistant ? tenant?.assistantData[assistant.id] : undefined;
      state?.sources.forEach((source) => { source.status = "succeeded"; source.fetched_at = isoNow(); });
      if (state) state.digest = { ...state.digest, content: `${state.digest.content}\n\nKnowledge import completed.`, created_at: isoNow() };
    }
    return HttpResponse.json<Schemas["JobResponse"]>(entry.job);
  }),
  http.get<{ id: string }, never, ApiResponse<Schemas["KnowledgeDigestResponse"]>>("*/api/assistants/:id/knowledge/digest", ({ request, params }) => {
    const tenant = selectedTenant(request);
    const digest = tenant.assistantData[String(params.id)]?.digest;
    return findAssistant(tenant, String(params.id)) && digest
      ? HttpResponse.json<Schemas["KnowledgeDigestResponse"]>(digest)
      : HttpResponse.json<ApiErrorResponse>({ detail: "Assistant not found" }, { status: 404 });
  }),
  http.put<{ id: string }, Schemas["KnowledgeDigestUpdate"], ApiResponse<Schemas["KnowledgeDigestResponse"]>>("*/api/assistants/:id/knowledge/digest", async ({ request, params }) => {
    const body = await requestJson<Schemas["KnowledgeDigestUpdate"]>(request);
    const tenant = selectedTenant(request);
    const state = tenant.assistantData[String(params.id)];
    if (!findAssistant(tenant, String(params.id)) || !state) return HttpResponse.json<ApiErrorResponse>({ detail: "Assistant not found" }, { status: 404 });
    state.digest = { ...state.digest, content: body.content, created_at: isoNow() };
    return HttpResponse.json<Schemas["KnowledgeDigestResponse"]>(state.digest);
  }),
  http.get<never, never, Schemas["ConversationListResponse"]>("*/api/conversations", ({ request }) => {
    const tenant = selectedTenant(request);
    const url = new URL(request.url);
    const channel = url.searchParams.get("channel");
    const status = url.searchParams.get("status");
    const hasReview = url.searchParams.get("has_review");
    const personId = url.searchParams.get("person_id");
    const limit = Number(url.searchParams.get("limit") ?? "20");
    const cursor = Number(url.searchParams.get("cursor") ?? "0");
    const filtered = tenant.conversations.filter((conversation) =>
      (!channel || conversation.channel === channel) && (!status || conversation.status === status) &&
      (hasReview !== "true" || conversation.needs_review) && (hasReview !== "false" || !conversation.needs_review) &&
      (!personId || conversation.person_id === personId));
    const items = filtered.slice(cursor, cursor + limit).map(toConversationSummary);
    const next = cursor + limit < filtered.length ? String(cursor + limit) : null;
    return HttpResponse.json<Schemas["ConversationListResponse"]>({ items, next_cursor: next });
  }),
  http.post<never, Schemas["ConversationBulkDeleteRequest"], ApiResponse<Schemas["ConversationBulkDeleteResponse"]>>("*/api/conversations/bulk-delete", async ({ request }) => {
    const tenant = selectedTenant(request);
    const { ids } = await requestJson<Schemas["ConversationBulkDeleteRequest"]>(request);
    let deleted = 0;
    let skippedInProgress = 0;
    for (const id of new Set(ids)) {
      const conversation = tenant.conversations.find((item) => item.id === id);
      if (conversation?.status === "active") {
        skippedInProgress += 1;
      } else if (removeConversation(tenant, id)) {
        deleted += 1;
      }
    }
    return HttpResponse.json<Schemas["ConversationBulkDeleteResponse"]>({
      deleted,
      skipped_in_progress: skippedInProgress,
    });
  }),
  http.delete<{ id: string }, never, ApiResponse<null>>("*/api/conversations/:id", ({ request, params }) => {
    const tenant = selectedTenant(request);
    const conversation = tenant.conversations.find(({ id }) => id === params.id);
    if (!conversation) {
      return HttpResponse.json<ApiErrorResponse>({ detail: "Conversation not found" }, { status: 404 });
    }
    if (conversation.status === "active") {
      return HttpResponse.json<ApiErrorResponse>({ detail: "Conversation is still in progress" }, { status: 409 });
    }
    removeConversation(tenant, String(params.id));
    return new HttpResponse(null, { status: 204 });
  }),
  http.get<{ id: string }, never, ApiResponse<Schemas["ConversationDetailResponse"]>>("*/api/conversations/:id", ({ request, params }) => {
    const conversation = selectedTenant(request).conversations.find(({ id }) => id === params.id);
    return conversation ? HttpResponse.json<Schemas["ConversationDetailResponse"]>(conversation) : HttpResponse.json<ApiErrorResponse>({ detail: "Conversation not found" }, { status: 404 });
  }),
  http.get<{ id: string }>("*/api/conversations/:id/recording", ({ request, params }) => {
    const conversation = selectedTenant(request).conversations.find(({ id }) => id === params.id);
    if (!conversation?.recording) return HttpResponse.json<ApiErrorResponse>({ detail: "Recording not found" }, { status: 404 });
    return new HttpResponse(silentWav, { headers: { "Content-Type": "audio/wav" } });
  }),
  http.post<{ id: string }, Schemas["ConversationReviewCreate"], ApiResponse<Schemas["ConversationReviewResponse"]>>("*/api/conversations/:id/reviews", async ({ request, params }) => {
    const body = await requestJson<Schemas["ConversationReviewCreate"]>(request);
    const tenant = selectedTenant(request);
    const conversation = tenant.conversations.find(({ id }) => id === params.id);
    if (!conversation) return HttpResponse.json<ApiErrorResponse>({ detail: "Conversation not found" }, { status: 404 });
    const merged = conversation.intakes.find(({ version }) => version === "merged");
    const data = merged?.data as { fields?: Record<string, Record<string, unknown>>; needs_review?: string[] } | undefined;
    const fieldEntry = data?.fields?.[body.field] ?? data?.fields?.[body.field.split(".").at(-1) ?? ""];
    if (fieldEntry) {
      fieldEntry.status = "human";
      fieldEntry.value = body.resolved_value;
      fieldEntry.needs_review = false;
      const sources = (fieldEntry.sources as Record<string, unknown> | undefined) ?? {};
      sources.human = { value: body.resolved_value, note: body.note ?? "", resolved_by: authUser.id, resolved_at: isoNow() };
      fieldEntry.sources = sources;
    }
    if (data) data.needs_review = Object.entries(data.fields ?? {}).filter(([, field]) => field.needs_review === true).map(([name]) => name);
    conversation.needs_review = Boolean(data?.needs_review?.length);
    return HttpResponse.json<Schemas["ConversationReviewResponse"]>({ id: crypto.randomUUID(), field: body.field, resolved_value: body.resolved_value, resolved_by: authUser.id, note: body.note ?? null, created_at: isoNow() }, { status: 201 });
  }),
  http.get<never, never, Schemas["ContactsResponse"]>("*/api/contacts", ({ request }) => {
    const tenant = selectedTenant(request);
    const url = new URL(request.url);
    const cursor = Number(url.searchParams.get("cursor") ?? "0");
    const limit = Number(url.searchParams.get("limit") ?? "50");
    const all = tenant.contacts.map(toContactSummary);
    return HttpResponse.json<Schemas["ContactsResponse"]>({ items: all.slice(cursor, cursor + limit), next_cursor: cursor + limit < all.length ? String(cursor + limit) : null });
  }),
  http.post<never, Schemas["BulkDeleteRequest"], ApiResponse<Schemas["BulkDeleteResponse"]>>("*/api/contacts/bulk-delete", async ({ request }) => {
    const tenant = selectedTenant(request);
    const { ids } = await requestJson<Schemas["BulkDeleteRequest"]>(request);
    const wanted = new Set(ids);
    const before = tenant.contacts.length;
    tenant.contacts = tenant.contacts.filter(({ id }) => !wanted.has(id));
    tenant.contacts.forEach((contact) => {
      if (contact.merged_into_id && wanted.has(contact.merged_into_id)) contact.merged_into_id = null;
    });
    tenant.conversations.forEach((conversation) => {
      if (conversation.person_id && wanted.has(conversation.person_id)) conversation.person_id = null;
    });
    tenant.tasks = tenant.tasks.map((task) => (task.person_id && wanted.has(task.person_id) ? { ...task, person_id: null } : task));
    return HttpResponse.json<Schemas["BulkDeleteResponse"]>({ deleted: before - tenant.contacts.length });
  }),
  http.get<{ id: string }, never, ApiResponse<Schemas["ContactDetailResponse"]>>("*/api/contacts/:id", ({ request, params }) => {
    const contact = selectedTenant(request).contacts.find(({ id }) => id === params.id);
    return contact ? HttpResponse.json<Schemas["ContactDetailResponse"]>(contact) : HttpResponse.json<ApiErrorResponse>({ detail: "Contact not found" }, { status: 404 });
  }),
  http.post<{ id: string }, Schemas["ContactMergeRequest"], ApiResponse<Schemas["ContactMergeResponse"]>>("*/api/contacts/:id/merge", async ({ request, params }) => {
    const { other_id } = await requestJson<Schemas["ContactMergeRequest"]>(request);
    const tenant = selectedTenant(request);
    const target = tenant.contacts.find(({ id }) => id === params.id);
    const other = tenant.contacts.find(({ id }) => id === other_id);
    if (!target || !other) return HttpResponse.json<ApiErrorResponse>({ detail: "Contact not found" }, { status: 404 });
    target.identities.push(...other.identities);
    target.active_facts.push(...other.active_facts);
    target.conversations.push(...other.conversations);
    target.open_tasks.push(...other.open_tasks);
    target.open_commitments.push(...other.open_commitments);
    other.merged_into_id = target.id;
    return HttpResponse.json<Schemas["ContactMergeResponse"]>({ survivor_id: target.id, merged_id: other.id });
  }),
  http.post<{ id: string }, Schemas["ContactSplitRequest"], ApiResponse<Schemas["ContactSplitResponse"]>>("*/api/contacts/:id/split", async ({ request, params }) => {
    const { identity_ids } = await requestJson<Schemas["ContactSplitRequest"]>(request);
    const tenant = selectedTenant(request);
    const source = tenant.contacts.find(({ id }) => id === params.id);
    if (!source) return HttpResponse.json<ApiErrorResponse>({ detail: "Contact not found" }, { status: 404 });
    const splitId = crypto.randomUUID();
    const identities = source.identities.filter(({ id }) => identity_ids.includes(id));
    source.identities = source.identities.filter(({ id }) => !identity_ids.includes(id));
    const split = { ...structuredClone(source), id: splitId, display_name: `${source.display_name} (split)`, identities, active_facts: [], conversations: [], open_tasks: [], open_commitments: [], created_at: isoNow(), updated_at: isoNow() };
    tenant.contacts.push(split);
    return HttpResponse.json<Schemas["ContactSplitResponse"]>({ person_id: source.id, split_person_id: splitId });
  }),
  http.get<never, never, Schemas["TaskListResponse"]>("*/api/tasks", ({ request }) => {
    const tenant = selectedTenant(request);
    const url = new URL(request.url);
    const status = url.searchParams.get("status");
    const kind = url.searchParams.get("kind");
    const assistantId = url.searchParams.get("assistant_id");
    return HttpResponse.json<Schemas["TaskListResponse"]>({
      items: tenant.tasks.filter((task) =>
        (!status || task.status === status)
        && (!kind || task.kind === kind)
        && (!assistantId || tenant.conversations.find((conversation) => conversation.id === task.conversation_id)?.assistant_id === assistantId),
      ),
    });
  }),
  http.post<never, Schemas["BulkDeleteRequest"], ApiResponse<Schemas["BulkDeleteResponse"]>>("*/api/tasks/bulk-delete", async ({ request }) => {
    const tenant = selectedTenant(request);
    const { ids } = await requestJson<Schemas["BulkDeleteRequest"]>(request);
    const wanted = new Set(ids);
    const before = tenant.tasks.length;
    tenant.tasks = tenant.tasks.filter(({ id }) => !wanted.has(id));
    tenant.conversations.forEach((conversation) => {
      conversation.tasks = conversation.tasks.filter(({ id }) => !wanted.has(id));
    });
    return HttpResponse.json<Schemas["BulkDeleteResponse"]>({ deleted: before - tenant.tasks.length });
  }),
  http.patch<{ id: string }, Schemas["TaskUpdate"], ApiResponse<Schemas["TaskSummary"]>>("*/api/tasks/:id", async ({ request, params }) => {
    const { status } = await requestJson<Schemas["TaskUpdate"]>(request);
    const task = selectedTenant(request).tasks.find(({ id }) => id === params.id);
    if (!task) return HttpResponse.json<ApiErrorResponse>({ detail: "Task not found" }, { status: 404 });
    task.status = status;
    return HttpResponse.json<Schemas["TaskSummary"]>(task);
  }),
  http.get<never, never, Schemas["KnowledgeGapsResponse"]>("*/api/knowledge-gaps", ({ request }) => {
    const tenant = selectedTenant(request);
    const url = new URL(request.url);
    const status = url.searchParams.get("status");
    const assistantId = url.searchParams.get("assistant_id");
    return HttpResponse.json<Schemas["KnowledgeGapsResponse"]>({
      items: tenant.gaps.filter((gap) =>
        (!status || gap.status === status)
        && (!assistantId || gap.assistant_id === assistantId),
      ),
    });
  }),
  http.patch<{ id: string }, Schemas["KnowledgeGapUpdate"], ApiResponse<Schemas["KnowledgeGapResponse"]>>("*/api/knowledge-gaps/:id", async ({ request, params }) => {
    const { status } = await requestJson<Schemas["KnowledgeGapUpdate"]>(request);
    const gap = selectedTenant(request).gaps.find(({ id }) => id === params.id);
    if (!gap) return HttpResponse.json<ApiErrorResponse>({ detail: "Knowledge gap not found" }, { status: 404 });
    gap.status = status;
    return HttpResponse.json<Schemas["KnowledgeGapResponse"]>(gap);
  }),
];
