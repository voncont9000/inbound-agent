import { http, HttpResponse } from "msw";
import type { Schemas } from "../api/client";
import { validateAssistantConfig } from "../lib/config";
import { authUser, templates } from "./fixtures";
import { mockDb, selectedTenant, selectedTenantId } from "./db";
import type { TenantFixture } from "./fixtures";
import assistantConfigSchema from "./assistant-config.schema.json";

const isoNow = () => new Date().toISOString();
const requestJson = async <T>(request: Request) => (await request.json()) as T;
type ApiErrorResponse = { detail: string } | Schemas["HTTPValidationError"];
type ApiResponse<T> = T | ApiErrorResponse;

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
  };
}

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
  http.get<never, never, Record<string, unknown>>("*/api/assistant-config/schema", () =>
    HttpResponse.json<Record<string, unknown>>(assistantConfigSchema),
  ),
  http.get<never, never, Schemas["TemplateResponse"][]>("*/api/templates", () => HttpResponse.json<Schemas["TemplateResponse"][]>(templates)),
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
    const assistant = { ...structuredClone(templateTenant.assistant), id: assistantId, name: `${body.tenant_name} assistant`, template: "blank", config: { ...structuredClone(templateTenant.assistant.config), business_name: body.tenant_name, services: {}, urgency_rules: [], policies: [], price_list: [], contacts: {}, knowledge: { faqs: [], max_digest_chars: 24000 } } };
    const fixture: TenantFixture = { ...structuredClone(templateTenant), tenant, assistant, conversations: [], contacts: [], gaps: [], tasks: [], sources: [], versions: [], digest: { ...templateTenant.digest, id: `${assistantId}-digest`, assistant_id: assistantId, content: "" } };
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
    return HttpResponse.json<Schemas["MeResponse"]>({ user: mockDb.user, tenant: mockDb.memberships.find(({ id }) => id === selectedTenantId(request)) ?? mockDb.memberships[0] });
  }),
  http.get<never, never, Schemas["AssistantListResponse"]>("*/api/assistants", ({ request }) => HttpResponse.json<Schemas["AssistantListResponse"]>({ items: [selectedTenant(request).assistant] })),
  http.post<never, Schemas["AssistantCreate"], ApiResponse<Schemas["AssistantResponse"]>>("*/api/assistants", async ({ request }) => {
    const body = await requestJson<Schemas["AssistantCreate"]>(request);
    const tenant = selectedTenant(request);
    const assistant: Schemas["AssistantResponse"] = { ...tenant.assistant, id: crypto.randomUUID(), name: body.name, template: body.template ?? null, config: body.config, version: 1, created_at: isoNow(), updated_at: isoNow() };
    tenant.assistant = assistant;
    tenant.versions = [{ id: `${assistant.id}-version-1`, version: 1, config: assistant.config, created_by: authUser.id, created_at: isoNow() }];
    return HttpResponse.json<Schemas["AssistantResponse"]>(assistant, { status: 201 });
  }),
  http.get<{ id: string }, never, ApiResponse<Schemas["AssistantResponse"]>>("*/api/assistants/:id", ({ request, params }) => {
    const assistant = selectedTenant(request).assistant;
    return assistant.id === params.id ? HttpResponse.json<Schemas["AssistantResponse"]>(assistant) : HttpResponse.json<ApiErrorResponse>({ detail: "Assistant not found" }, { status: 404 });
  }),
  http.put<{ id: string }, Schemas["AssistantUpdate"], ApiResponse<Schemas["AssistantResponse"]>>("*/api/assistants/:id", async ({ request, params }) => {
    const body = await requestJson<Schemas["AssistantUpdate"]>(request);
    const tenant = selectedTenant(request);
    if (tenant.assistant.id !== params.id) return HttpResponse.json<ApiErrorResponse>({ detail: "Assistant not found" }, { status: 404 });
    const errors = validateAssistantConfig(body.config);
    if (Object.keys(errors).length) {
      return HttpResponse.json<Schemas["HTTPValidationError"]>({ detail: Object.entries(errors).map(([path, msg]) => ({ loc: ["body", "config", ...path.split(".").map((part) => /^\d+$/.test(part) ? Number(part) : part)], msg, type: "value_error" })) }, { status: 422 });
    }
    const updated = { ...tenant.assistant, name: body.name ?? tenant.assistant.name, template: body.template ?? tenant.assistant.template, config: body.config, version: tenant.assistant.version + 1, updated_at: isoNow() };
    tenant.assistant = updated;
    tenant.versions.push({ id: `${updated.id}-version-${updated.version}`, version: updated.version, config: structuredClone(updated.config), created_by: authUser.id, created_at: updated.updated_at });
    return HttpResponse.json<Schemas["AssistantResponse"]>(updated);
  }),
  http.get<{ id: string }, never, Schemas["AssistantVersionsResponse"]>("*/api/assistants/:id/versions", ({ request }) => HttpResponse.json<Schemas["AssistantVersionsResponse"]>({ items: selectedTenant(request).versions })),
  http.get<{ id: string }, never, Schemas["KnowledgeSourcesResponse"]>("*/api/assistants/:id/knowledge/sources", ({ request }) => HttpResponse.json<Schemas["KnowledgeSourcesResponse"]>({ items: selectedTenant(request).sources })),
  http.post<{ id: string }, Schemas["KnowledgeSourceCreate"] | { file: string }, ApiResponse<Schemas["KnowledgeSourceResponse"]>>("*/api/assistants/:id/knowledge/sources", async ({ request, params }) => {
    const tenant = selectedTenant(request);
    const form = request.headers.get("content-type")?.includes("multipart/form-data");
    let kind: "url" | "file" = "url";
    let uri = "";
    if (form) {
      const data = await request.formData();
      const file = data.get("file");
      if (file && typeof file !== "string") { kind = "file"; uri = file.name; }
    } else {
      const body = await requestJson<Schemas["KnowledgeSourceCreate"]>(request);
      uri = body.uri;
    }
    const source: Schemas["KnowledgeSourceResponse"] = { id: crypto.randomUUID(), assistant_id: String(params.id), kind, uri, status: "pending", manifest: {}, fetched_at: null, created_at: isoNow() };
    tenant.sources.push(source);
    return HttpResponse.json<Schemas["KnowledgeSourceResponse"]>(source, { status: 201 });
  }),
  http.post<{ id: string }, never, Schemas["JobResponse"]>("*/api/assistants/:id/knowledge/import", ({ request, params }) => {
    const id = crypto.randomUUID();
    const job: Schemas["JobResponse"] = { id, kind: "knowledge_import", status: "queued", payload: { assistant_id: params.id }, result: null, error: null, attempts: 0, run_after: isoNow(), created_at: isoNow() };
    mockDb.jobs[id] = { tenantId: selectedTenantId(request), assistantId: String(params.id), polls: 0, job };
    return HttpResponse.json<Schemas["JobResponse"]>(job, { status: 202 });
  }),
  http.get<{ id: string }, never, ApiResponse<Schemas["JobResponse"]>>("*/api/jobs/:id", ({ request, params }) => {
    const entry = mockDb.jobs[String(params.id)];
    if (!entry || entry.tenantId !== selectedTenantId(request)) return HttpResponse.json<ApiErrorResponse>({ detail: "Job not found" }, { status: 404 });
    entry.polls += 1;
    const status = entry.polls < 2 ? "queued" : entry.polls < 3 ? "running" : "succeeded";
    entry.job = { ...entry.job, status, attempts: entry.polls, ...(status === "succeeded" ? { result: { imported: entry.assistantId } } : {}) };
    if (status === "succeeded") {
      const tenant = mockDb.tenants.find(({ tenant: { id } }) => id === entry.tenantId);
      tenant?.sources.forEach((source) => { source.status = "succeeded"; source.fetched_at = isoNow(); });
      if (tenant) tenant.digest = { ...tenant.digest, content: `${tenant.digest.content}\n\nKnowledge import completed.`, created_at: isoNow() };
    }
    return HttpResponse.json<Schemas["JobResponse"]>(entry.job);
  }),
  http.get<{ id: string }, never, Schemas["KnowledgeDigestResponse"]>("*/api/assistants/:id/knowledge/digest", ({ request }) => HttpResponse.json<Schemas["KnowledgeDigestResponse"]>(selectedTenant(request).digest)),
  http.put<{ id: string }, Schemas["KnowledgeDigestUpdate"], Schemas["KnowledgeDigestResponse"]>("*/api/assistants/:id/knowledge/digest", async ({ request }) => {
    const body = await requestJson<Schemas["KnowledgeDigestUpdate"]>(request);
    const tenant = selectedTenant(request);
    tenant.digest = { ...tenant.digest, content: body.content, created_at: isoNow() };
    return HttpResponse.json<Schemas["KnowledgeDigestResponse"]>(tenant.digest);
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
  http.get<{ id: string }, never, ApiResponse<Schemas["ConversationDetailResponse"]>>("*/api/conversations/:id", ({ request, params }) => {
    const conversation = selectedTenant(request).conversations.find(({ id }) => id === params.id);
    return conversation ? HttpResponse.json<Schemas["ConversationDetailResponse"]>(conversation) : HttpResponse.json<ApiErrorResponse>({ detail: "Conversation not found" }, { status: 404 });
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
  http.get<never, never, Schemas["TaskListResponse"]>("*/api/tasks", ({ request }) => HttpResponse.json<Schemas["TaskListResponse"]>({ items: selectedTenant(request).tasks })),
  http.patch<{ id: string }, Schemas["TaskUpdate"], ApiResponse<Schemas["TaskSummary"]>>("*/api/tasks/:id", async ({ request, params }) => {
    const { status } = await requestJson<Schemas["TaskUpdate"]>(request);
    const task = selectedTenant(request).tasks.find(({ id }) => id === params.id);
    if (!task) return HttpResponse.json<ApiErrorResponse>({ detail: "Task not found" }, { status: 404 });
    task.status = status;
    return HttpResponse.json<Schemas["TaskSummary"]>(task);
  }),
  http.get<never, never, Schemas["KnowledgeGapsResponse"]>("*/api/knowledge-gaps", ({ request }) => HttpResponse.json<Schemas["KnowledgeGapsResponse"]>({ items: selectedTenant(request).gaps })),
  http.patch<{ id: string }, Schemas["KnowledgeGapUpdate"], ApiResponse<Schemas["KnowledgeGapResponse"]>>("*/api/knowledge-gaps/:id", async ({ request, params }) => {
    const { status } = await requestJson<Schemas["KnowledgeGapUpdate"]>(request);
    const gap = selectedTenant(request).gaps.find(({ id }) => id === params.id);
    if (!gap) return HttpResponse.json<ApiErrorResponse>({ detail: "Knowledge gap not found" }, { status: 404 });
    gap.status = status;
    return HttpResponse.json<Schemas["KnowledgeGapResponse"]>(gap);
  }),
];
