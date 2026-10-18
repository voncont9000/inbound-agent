import { useMemo, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import type { Schemas } from "../api/client";
import { request } from "../api/request";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Badge, EmptyState, ErrorState, LoadingState } from "../components/States";

type IntakeRecord = Record<string, unknown>;
type IntakeField = {
  value?: unknown;
  status?: string;
  confidence?: number;
  needs_review?: boolean;
  confirmed?: boolean;
  confirmed_at?: string | null;
  confirmed_by_caller?: boolean;
  sources?: Record<string, { value?: unknown; confirmed_at?: string; confidence?: number; evidence?: string; note?: string; resolved_by?: string; resolved_at?: string }>;
  alternative?: unknown;
};

const channels = ["phone", "web", "gmail", "whatsapp"] as const;
const statuses = ["active", "completed", "failed"] as const;
const formatDate = (value?: string | null) => value ? new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "—";

function conversationQuery(filters: { channel: string; status: string; reviewOnly: boolean }, cursor?: string | null) {
  const params = new URLSearchParams();
  if (filters.channel !== "all") params.set("channel", filters.channel);
  if (filters.status !== "all") params.set("status", filters.status);
  if (filters.reviewOnly) params.set("has_review", "true");
  if (cursor) params.set("cursor", cursor);
  params.set("limit", "20");
  return `/conversations?${params.toString()}`;
}

export function InboxPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const channel = searchParams.get("channel") ?? "all";
  const status = searchParams.get("status") ?? "all";
  const reviewOnly = searchParams.get("has_review") === "true";
  const filters = { channel, status, reviewOnly };
  const conversations = useInfiniteQuery({
    queryKey: ["conversations", filters],
    queryFn: ({ pageParam }) => request<Schemas["ConversationListResponse"]>(conversationQuery(filters, pageParam)),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
  });
  const reviewed = useQuery({
    queryKey: ["conversations-needing-review", channel, status],
    queryFn: () => {
      const params = new URLSearchParams({ has_review: "true" });
      if (channel !== "all") params.set("channel", channel);
      if (status !== "all") params.set("status", status);
      return request<Schemas["ConversationListResponse"]>(`/conversations?${params}`);
    },
  });
  const needsReviewIds = useMemo(() => new Set(reviewed.data?.items.map(({ id }) => id) ?? []), [reviewed.data]);
  const rows = conversations.data?.pages.flatMap(({ items }) => items) ?? [];
  function updateParam(key: string, value: string) {
    const next = new URLSearchParams(searchParams);
    if (!value || value === "all" || (key === "has_review" && value !== "true")) next.delete(key);
    else next.set(key, value);
    setSearchParams(next);
  }
  return <main className="page" id="main-content">
    <p className="eyebrow">Operator</p><h1>Inbox</h1>
    <div className="toolbar">
      <div className="field"><label htmlFor="filter-channel">Channel</label><select id="filter-channel" value={channel} onChange={(event) => updateParam("channel", event.target.value)}><option value="all">All channels</option>{channels.map((value) => <option key={value} value={value}>{value}</option>)}</select></div>
      <div className="field"><label htmlFor="filter-status">Status</label><select id="filter-status" value={status} onChange={(event) => updateParam("status", event.target.value)}><option value="all">All statuses</option>{statuses.map((value) => <option key={value} value={value}>{value}</option>)}</select></div>
      <label className="check-row"><input type="checkbox" checked={reviewOnly} onChange={(event) => updateParam("has_review", event.target.checked ? "true" : "false")} />Needs review only</label>
    </div>
    {conversations.isLoading && <LoadingState />}
    {conversations.error && <ErrorState message="Conversations could not be loaded." />}
    {!conversations.isLoading && !rows.length && <EmptyState>No conversations match these filters.</EmptyState>}
    {rows.length > 0 && <div className="table-wrap"><table><thead><tr><th>Channel</th><th>Status</th><th>Contact</th><th>Summary</th><th>Started</th><th>Review</th></tr></thead><tbody>
      {rows.map((conversation) => <tr key={conversation.id}>
        <td><Badge>{channelIcon[conversation.channel]} {conversation.channel}</Badge></td>
        <td>{conversation.status}</td>
        <td>{conversation.person_id ? <Link to={`/contacts/${conversation.person_id}`}>{conversation.external_ref}</Link> : conversation.external_ref}</td>
        <td><Link to={`/conversations/${conversation.id}`}>{conversation.summary ?? "No summary"}</Link></td>
        <td>{formatDate(conversation.started_at)}</td>
        <td>{needsReviewIds.has(conversation.id) ? <Badge tone="warning">Needs review</Badge> : "—"}</td>
      </tr>)}
    </tbody></table></div>}
    {conversations.hasNextPage && <button type="button" onClick={() => void conversations.fetchNextPage()} disabled={conversations.isFetchingNextPage}>{conversations.isFetchingNextPage ? "Loading…" : "Load more"}</button>}
  </main>;
}

const channelIcon: Record<string, string> = { phone: "☎", web: "◎", gmail: "✉", whatsapp: "◉" };

export function ConversationPage() {
  const { id = "" } = useParams();
  const queryClient = useQueryClient();
  const [openReview, setOpenReview] = useState("");
  const detailQuery = useQuery({ queryKey: ["conversation", id], queryFn: () => request<Schemas["ConversationDetailResponse"]>(`/conversations/${id}`) });
  const assistantId = detailQuery.data?.assistant_id ?? "";
  const assistantQuery = useQuery({ queryKey: ["assistant", assistantId], queryFn: () => request<Schemas["AssistantResponse"]>(`/assistants/${assistantId}`), enabled: Boolean(assistantId) });
  const detail = detailQuery.data;
  const intakes = useMemo(() => {
    const result: Partial<Record<"live" | "post_call" | "merged", IntakeRecord>> = {};
    detail?.intakes.forEach((intake) => { result[intake.version] = intake.data as IntakeRecord; });
    return result;
  }, [detail]);
  const liveFields = asRecord(intakes.live?.fields) as Record<string, IntakeField>;
  const postFields = asRecord(intakes.post_call?.fields) as Record<string, IntakeField>;
  const mergedData = intakes.merged;
  const mergedFields = asRecord(mergedData?.fields) as Record<string, IntakeField>;
  const fieldNames = useMemo(() => {
    const service = String(mergedData?.service ?? intakes.live?.service ?? intakes.post_call?.service ?? "");
    const ordered = assistantQuery.data?.config.services[service]?.fields.map(({ name }) => name) ?? [];
    const discovered = [...Object.keys(liveFields), ...Object.keys(postFields), ...Object.keys(mergedFields)];
    return [...new Set([...ordered, ...discovered])];
  }, [assistantQuery.data, intakes.live, intakes.post_call, mergedData, liveFields, postFields, mergedFields]);
  const review = useMutation({
    mutationFn: (input: { field: string; resolved_value: unknown; note?: string }) => request(`/conversations/${id}/reviews`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) }),
    onSuccess: async () => {
      setOpenReview("");
      await queryClient.invalidateQueries({ queryKey: ["conversation", id] });
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      await queryClient.invalidateQueries({ queryKey: ["conversations-needing-review"] });
    },
  });
  const patchTask = useMutation({
    mutationFn: (input: { id: string; status: Schemas["TaskUpdate"]["status"] }) => request(`/tasks/${input.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: input.status }) }),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ["conversation", id] }); void queryClient.invalidateQueries({ queryKey: ["tasks"] }); },
  });
  if (detailQuery.isLoading) return <main className="page" id="main-content"><LoadingState /></main>;
  if (detailQuery.error || !detail) return <main className="page" id="main-content"><ErrorState message="Conversation could not be loaded." /></main>;
  const contact = detail.person_id ? <Link to={`/contacts/${detail.person_id}`}>{detail.external_ref}</Link> : detail.external_ref;
  return <main className="page" id="main-content">
    <p className="eyebrow">Conversation · {detail.channel}</p><h1>{detail.summary ?? "Conversation details"}</h1>
    <div className="panel card-grid"><div><strong>Status</strong><p>{detail.status}</p></div><div><strong>Contact</strong><p>{contact}</p></div><div><strong>Started</strong><p>{formatDate(detail.started_at)}</p></div><div><strong>Ended</strong><p>{formatDate(detail.ended_at)}</p></div><div><strong>End reason</strong><p>{detail.end_reason ?? "—"}</p></div></div>
    <section className="panel"><h2>Transcript</h2><div className="transcript">{detail.messages.map((message) => <article className={`message message-${message.role}`} key={message.id}><strong>{message.role}</strong><div>{message.text}</div><time>{formatDate(message.ts)}</time></article>)}</div></section>
    <section className="panel stack"><h2>Intake comparison</h2>
      {!intakes.merged && <p className="muted">Merged intake is unavailable. Live and post-call values are shown where available.</p>}
      <div className="intake-grid">
        <div className="intake-head">Field</div><div className="intake-head">Live</div><div className="intake-head">Post-call</div><div className="intake-head">Merged</div>
        {fieldNames.map((name) => {
          const service = String(mergedData?.service ?? intakes.live?.service ?? intakes.post_call?.service ?? "");
          const path = `${service}.${name}`;
          const fieldLabel = assistantFieldLabel(assistantQuery.data, mergedData, name) ?? name;
          const live = liveFields[name];
          const post = postFields[name];
          const merged = mergedFields[name];
          const flagged = merged?.needs_review === true || Boolean((mergedData?.needs_review as unknown[] | undefined)?.some((item) => String(item) === name || String(item) === path));
          const candidates = reviewCandidates(live, post, merged);
          const liveValue = live?.value;
          return <div key={name} className="intake-row">
            <div><strong>{fieldLabel}</strong>{isCritical(assistantQuery.data, mergedData, name) && <span className="muted"> · critical</span>}</div>
            <div>{displayValue(liveValue)}{live?.confirmed && <p><Badge tone="success">Confirmed by caller</Badge></p>}</div>
            <div>{displayValue(post?.value)}{typeof post?.confidence === "number" && <p className="muted">Confidence {post.confidence.toFixed(2)}</p>}</div>
            <div>
              {merged ? <><strong>{displayValue(merged.value)}</strong>{typeof merged.confidence === "number" && <p className="muted">Confidence {merged.confidence.toFixed(2)}</p>}<p><Badge>{mergedSource(merged)}</Badge></p>{merged.status === "conflict" && merged.alternative !== undefined && <p>Alternative: {displayValue(merged.alternative)}</p>}{flagged && <Badge tone="warning">Needs review</Badge>}{merged.status === "human" && merged.sources?.human?.note && <p>Note: {merged.sources.human.note}</p>}</> : <span>—</span>}
              <div><button type="button" onClick={() => setOpenReview(openReview === name ? "" : name)}>{openReview === name ? "Close review" : "Review field"}</button></div>
              {openReview === name && <ReviewForm field={name} candidates={candidates} live={live} post={post} merged={merged} onSubmit={(input) => review.mutate(input)} pending={review.isPending} />}
            </div>
          </div>;
        })}
        {!fieldNames.length && <div>—</div>}
      </div>
    </section>
    <section className="panel"><h2>Tasks</h2>{detail.tasks.map((task) => <div className="list-row" key={task.id}><span>{task.kind} · {task.source} · {compactPayload(task.payload)}</span><label className="sr-only" htmlFor={`conversation-task-${task.id}`}>Status for {task.kind}</label><select id={`conversation-task-${task.id}`} value={task.status} onChange={(event) => patchTask.mutate({ id: task.id, status: event.target.value as Schemas["TaskUpdate"]["status"] })}><option value="open">open</option><option value="done">done</option><option value="dismissed">dismissed</option></select></div>)}</section>
    <MetricsCard metrics={detail.metrics} estimatedCost={detail.estimated_cost} />
  </main>;
}

function ReviewForm({ field, candidates, live, post, merged, onSubmit, pending }: {
  field: string;
  candidates: Array<{ label: string; value: unknown }>;
  live?: IntakeField;
  post?: IntakeField;
  merged?: IntakeField;
  onSubmit: (input: { field: string; resolved_value: unknown; note?: string }) => void;
  pending: boolean;
}) {
  const [selected, setSelected] = useState("0");
  const [otherValue, setOtherValue] = useState("");
  const [note, setNote] = useState("");
  const [override, setOverride] = useState(false);
  const chosen = selected === "other" ? otherValue : candidates[Number(selected)]?.value;
  const liveConfirmed = Boolean(live?.confirmed || live?.confirmed_by_caller || merged?.confirmed_by_caller || post?.confirmed_by_caller);
  const confirmedValue = live?.confirmed || live?.confirmed_by_caller ? live.value : post?.confirmed_by_caller ? post.value : merged?.confirmed_by_caller ? merged.value : undefined;
  const differsFromConfirmed = liveConfirmed && JSON.stringify(chosen) !== JSON.stringify(confirmedValue);
  const canSubmit = selected !== "other" || otherValue.trim() !== "";
  return <form className="review-form" onSubmit={(event) => {
    event.preventDefault();
    if (!canSubmit || (differsFromConfirmed && !override)) return;
    onSubmit({ field, resolved_value: chosen, ...(note.trim() ? { note } : {}) });
  }}>
    <strong>Resolve {field}</strong>
    {candidates.map((candidate, index) => <label className="radio-row" key={`${candidate.label}-${index}`}><input type="radio" name={`resolve-${field}`} value={index} checked={selected === String(index)} onChange={() => setSelected(String(index))} /><span>{candidate.label}: {displayValue(candidate.value)}</span></label>)}
    <label className="radio-row"><input type="radio" name={`resolve-${field}`} value="other" checked={selected === "other"} onChange={() => setSelected("other")} /><span>Other value</span></label>
    {selected === "other" && <div className="field"><label htmlFor={`other-${field}`}>Other value</label><input id={`other-${field}`} value={otherValue} onChange={(event) => setOtherValue(event.target.value)} /></div>}
    <div className="field"><label htmlFor={`note-${field}`}>Note (optional)</label><textarea id={`note-${field}`} value={note} onChange={(event) => setNote(event.target.value)} /></div>
    {differsFromConfirmed && <label className="check-row"><input type="checkbox" checked={override} onChange={(event) => setOverride(event.target.checked)} />I'm overriding a value the caller confirmed</label>}
    <button type="submit" className="button-primary" disabled={pending || !canSubmit || (differsFromConfirmed && !override)}>Submit review</button>
  </form>;
}

function reviewCandidates(live?: IntakeField, post?: IntakeField, merged?: IntakeField) {
  const entries: Array<{ label: string; value: unknown }> = [];
  const push = (label: string, value: unknown) => {
    if (value === undefined || entries.some((entry) => JSON.stringify(entry.value) === JSON.stringify(value))) return;
    entries.push({ label, value });
  };
  push("Live", live?.value);
  push("Post-call", post?.value);
  push("Alternative", merged?.alternative);
  push("Caller ID", merged?.sources?.caller_id?.value);
  push("Merged", merged?.value);
  return entries;
}

function assistantFieldLabel(assistant: Schemas["AssistantResponse"] | undefined, merged: IntakeRecord | undefined, field: string) {
  const service = String(merged?.service ?? "");
  return assistant?.config.services[service]?.fields.find(({ name }) => name === field)?.label;
}

function isCritical(assistant: Schemas["AssistantResponse"] | undefined, merged: IntakeRecord | undefined, field: string) {
  const service = String(merged?.service ?? "");
  return assistant?.config.services[service]?.fields.find(({ name }) => name === field)?.critical;
}

function mergedSource(field: IntakeField): string {
  if (field.status === "human" || field.sources?.human) return "human";
  if (field.status === "agreed") return "live + post_call";
  if (field.status === "live_only") return "live";
  if (["corrected", "filled", "filled_confirmed"].includes(field.status ?? "")) return "post_call";
  if (field.status === "conflict") return "live (conflict)";
  if (field.status === "caller_id") return "caller ID";
  return field.status ?? "merged";
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.length ? value.map(displayValue).join(", ") : "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function compactPayload(payload: Record<string, unknown>) {
  const summary = payload.reason ?? payload.summary ?? payload.details;
  return summary ? String(summary) : JSON.stringify(payload);
}

function MetricsCard({ metrics, estimatedCost }: { metrics: Record<string, unknown>; estimatedCost: Record<string, unknown> | null }) {
  const latency = asRecord(metrics.turn_latency_summary);
  const endLatency = asRecord(metrics.turn_end_latency_summary);
  const cost = estimatedCost ?? {};
  const components = asRecord(cost.components);
  const money = (value: unknown) => typeof value === "number" ? `${String(cost.currency ?? "")} ${value.toFixed(3)}`.trim() : "—";
  const seconds = (value: unknown) => typeof value === "number" ? `${value.toFixed(2)}s` : "—";
  return <section className="panel"><h2>Call metrics</h2><div className="card-grid">
    <div><strong>Duration</strong><p>{typeof metrics.call_duration_seconds === "number" ? `${Math.round(metrics.call_duration_seconds)} seconds` : "—"}</p></div>
    <div><strong>Turn latency p50 / p95</strong><p>{seconds(latency.p50_seconds)} / {seconds(latency.p95_seconds)}</p></div>
    <div><strong>Turn-end latency p50 / p95</strong><p>{seconds(endLatency.p50_seconds)} / {seconds(endLatency.p95_seconds)}</p></div>
    <div><strong>Estimated total cost</strong><p>{money(cost.total)}</p></div>
    <div><strong>Cost components</strong><ul>{Object.entries(components).map(([name, value]) => <li key={name}>{name}: {money(value)}</li>)}{!Object.keys(components).length && <li>—</li>}</ul></div>
  </div></section>;
}

export function ContactsPage() {
  const contacts = useInfiniteQuery({
    queryKey: ["contacts"],
    queryFn: ({ pageParam }) => request<Schemas["ContactsResponse"]>(`/contacts?limit=50${pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : ""}`),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
  });
  const [filter, setFilter] = useState("");
  const rows = (contacts.data?.pages.flatMap(({ items }) => items) ?? []).filter(({ display_name }) => display_name.toLowerCase().includes(filter.toLowerCase()));
  return <main className="page" id="main-content"><p className="eyebrow">People</p><h1>Contacts</h1>
    <div className="field"><label htmlFor="contact-filter">Filter by name</label><input id="contact-filter" value={filter} onChange={(event) => setFilter(event.target.value)} /></div>
    {contacts.isLoading && <LoadingState />}{contacts.error && <ErrorState message="Contacts could not be loaded." />}
    {!rows.length && !contacts.isLoading && <EmptyState>No contacts found.</EmptyState>}
    <div className="table-wrap"><table><thead><tr><th>Name</th><th>Created</th><th>Updated</th><th>Status</th></tr></thead><tbody>{rows.map((contact) => <tr key={contact.id}><td><Link to={`/contacts/${contact.id}`}>{contact.display_name}</Link></td><td>{formatDate(contact.created_at)}</td><td>{formatDate(contact.updated_at)}</td><td>{contact.merged_into_id ? <Badge>merged</Badge> : "Active"}</td></tr>)}</tbody></table></div>
    {contacts.hasNextPage && <button type="button" onClick={() => void contacts.fetchNextPage()} disabled={contacts.isFetchingNextPage}>Load more</button>}
  </main>;
}

export function ContactPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [otherId, setOtherId] = useState("");
  const [dialog, setDialog] = useState<"merge" | "split" | "">("");
  const [selectedIdentities, setSelectedIdentities] = useState<string[]>([]);
  const detailQuery = useQuery({ queryKey: ["contact", id], queryFn: () => request<Schemas["ContactDetailResponse"]>(`/contacts/${id}`) });
  const contactsQuery = useQuery({ queryKey: ["contacts-for-merge"], queryFn: () => request<Schemas["ContactsResponse"]>("/contacts?limit=100") });
  const detail = detailQuery.data;
  const merge = useMutation({
    mutationFn: () => request<Schemas["ContactMergeResponse"]>(`/contacts/${id}/merge`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ other_id: otherId }) }),
    onSuccess: async ({ survivor_id }) => { setDialog(""); await queryClient.invalidateQueries({ queryKey: ["contacts"] }); navigate(`/contacts/${survivor_id}`); },
  });
  const split = useMutation({
    mutationFn: () => request<Schemas["ContactSplitResponse"]>(`/contacts/${id}/split`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ identity_ids: selectedIdentities }) }),
    onSuccess: async ({ split_person_id }) => { setDialog(""); await queryClient.invalidateQueries({ queryKey: ["contacts"] }); navigate(`/contacts/${split_person_id}`); },
  });
  if (detailQuery.isLoading) return <main className="page" id="main-content"><LoadingState /></main>;
  if (detailQuery.error || !detail) return <main className="page" id="main-content"><ErrorState message="Contact could not be loaded." /></main>;
  const mergeChoices = (contactsQuery.data?.items ?? []).filter((contact) => contact.id !== id && !contact.merged_into_id);
  return <main className="page" id="main-content"><p className="eyebrow">Contact</p><h1>{detail.display_name}</h1>
    <div className="toolbar"><div className="field"><label htmlFor="merge-contact">Merge another contact into this one</label><select id="merge-contact" value={otherId} onChange={(event) => setOtherId(event.target.value)}><option value="">Choose a contact</option>{mergeChoices.map((contact) => <option key={contact.id} value={contact.id}>{contact.display_name}</option>)}</select></div><button type="button" disabled={!otherId} onClick={() => setDialog("merge")}>Merge contact</button></div>
    <section className="panel"><h2>Identities</h2>{detail.identities.map((identity) => <div className="list-row" key={identity.id}><label className="check-row"><input type="checkbox" checked={selectedIdentities.includes(identity.id)} onChange={(event) => setSelectedIdentities((current) => event.target.checked ? [...current, identity.id] : current.filter((item) => item !== identity.id))} />{identity.type}: {identity.value_normalized}</label><span>{identity.verified ? "Verified" : "Unverified"} · {identity.source} · {identity.confidence.toFixed(2)}</span></div>)}<button type="button" disabled={!selectedIdentities.length} onClick={() => setDialog("split")}>Split selected identities</button></section>
    <section className="panel"><h2>Active facts</h2>{detail.active_facts.map((fact) => <div className="list-row" key={fact.id}><span><strong>{fact.predicate}</strong>: {displayValue(fact.value)}<br /><span className="muted">{fact.evidence} · confidence {fact.confidence.toFixed(2)} · since {formatDate(fact.valid_from)}</span></span></div>)}</section>
    <section className="panel"><h2>Conversations</h2>{detail.conversations.map((conversation) => <div className="list-row" key={conversation.id}><Link to={`/conversations/${conversation.id}`}>{conversation.channel} · {conversation.summary ?? conversation.external_ref}</Link><span>{formatDate(conversation.started_at)}</span></div>)}</section>
    <section className="panel"><h2>Open tasks</h2>{detail.open_tasks.map((task) => <p key={task.id}>{task.kind} · {task.status} · {compactPayload(task.payload)}</p>)}</section>
    <section className="panel"><h2>Open commitments</h2>{detail.open_commitments.map((commitment) => <p key={commitment.id}>{commitment.what} · {commitment.due_at ? formatDate(commitment.due_at) : "No due date"} · {commitment.status}</p>)}</section>
    <section className="panel"><h2>Context card preview</h2><pre className="context-card">{detail.context_card}</pre></section>
    {dialog === "merge" && <ConfirmDialog title={`Merge ${mergeChoices.find(({ id: other }) => other === otherId)?.display_name ?? "contact"} into ${detail.display_name}?`} onCancel={() => setDialog("")} onConfirm={() => merge.mutate()} confirmLabel="Merge">Identities, facts and conversations move to {detail.display_name}. This is audited and can be reversed by a split.</ConfirmDialog>}
    {dialog === "split" && <ConfirmDialog title="Split selected identities?" onCancel={() => setDialog("")} onConfirm={() => split.mutate()} confirmLabel="Split">The selected identities will move to a new contact profile.</ConfirmDialog>}
  </main>;
}

export function TasksPage() {
  const queryClient = useQueryClient();
  const tasks = useQuery({ queryKey: ["tasks"], queryFn: () => request<Schemas["TaskListResponse"]>("/tasks") });
  const [statusFilter, setStatusFilter] = useState("all");
  const [kindFilter, setKindFilter] = useState("all");
  const update = useMutation({
    mutationFn: (input: { id: string; status: Schemas["TaskUpdate"]["status"] }) => request(`/tasks/${input.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: input.status }) }),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ["tasks"] }); },
  });
  const kinds = [...new Set(tasks.data?.items.map(({ kind }) => kind) ?? [])];
  const rows = (tasks.data?.items ?? []).filter((task) => (statusFilter === "all" || task.status === statusFilter) && (kindFilter === "all" || task.kind === kindFilter));
  return <main className="page" id="main-content"><p className="eyebrow">Follow-up</p><h1>Tasks</h1>
    <div className="toolbar"><div className="field"><label htmlFor="task-status-filter">Status</label><select id="task-status-filter" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="all">All statuses</option>{["open", "done", "dismissed"].map((status) => <option key={status}>{status}</option>)}</select></div><div className="field"><label htmlFor="task-kind-filter">Kind</label><select id="task-kind-filter" value={kindFilter} onChange={(event) => setKindFilter(event.target.value)}><option value="all">All kinds</option>{kinds.map((kind) => <option key={kind}>{kind}</option>)}</select></div></div>
    {tasks.isLoading && <LoadingState />}{tasks.error && <ErrorState message="Tasks could not be loaded." />}{!rows.length && !tasks.isLoading && <EmptyState>No tasks match these filters.</EmptyState>}
    <div className="table-wrap"><table><thead><tr><th>Kind</th><th>Status</th><th>Source</th><th>Due</th><th>Details</th><th>Update status</th></tr></thead><tbody>{rows.map((task) => <tr key={task.id}><td>{task.kind}</td><td>{task.status}</td><td>{task.source}</td><td>{formatDate(task.due_at)}</td><td>{compactPayload(task.payload)}</td><td><label className="sr-only" htmlFor={`task-status-${task.id}`}>Status for {task.kind}</label><select id={`task-status-${task.id}`} value={task.status} onChange={(event) => update.mutate({ id: task.id, status: event.target.value as Schemas["TaskUpdate"]["status"] })}><option value="open">open</option><option value="done">done</option><option value="dismissed">dismissed</option></select></td></tr>)}</tbody></table></div>
  </main>;
}

export function KnowledgeGapsPage() {
  const queryClient = useQueryClient();
  const gaps = useQuery({ queryKey: ["knowledge-gaps"], queryFn: () => request<Schemas["KnowledgeGapsResponse"]>("/knowledge-gaps") });
  const [filter, setFilter] = useState("open");
  const update = useMutation({
    mutationFn: (input: { id: string; status: Schemas["KnowledgeGapUpdate"]["status"] }) => request(`/knowledge-gaps/${input.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: input.status }) }),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ["knowledge-gaps"] }); },
  });
  const rows = (gaps.data?.items ?? []).filter(({ status }) => filter === "all" || status === filter);
  return <main className="page" id="main-content"><p className="eyebrow">Knowledge</p><h1>Knowledge gaps</h1>
    <div className="field"><label htmlFor="gap-filter">Status</label><select id="gap-filter" value={filter} onChange={(event) => setFilter(event.target.value)}><option value="all">All statuses</option><option value="open">Open</option><option value="answered">Answered</option><option value="dismissed">Dismissed</option></select></div>
    {gaps.isLoading && <LoadingState />}{gaps.error && <ErrorState message="Knowledge gaps could not be loaded." />}{!rows.length && !gaps.isLoading && <EmptyState>No knowledge gaps match this status.</EmptyState>}
    <div className="card-grid">{rows.map((gap) => <article className="panel stack" key={gap.id}><div className="button-row"><Badge tone={gap.status === "open" ? "warning" : "success"}>{gap.status}</Badge><span className="muted">{formatDate(gap.created_at)}</span></div><h2>{gap.question}</h2><p>{gap.evidence}</p><Link to={`/conversations/${gap.conversation_id}`}>View conversation</Link><div className="button-row">{gap.status !== "answered" && <button type="button" onClick={() => update.mutate({ id: gap.id, status: "answered" })}>Mark answered</button>}{gap.status !== "dismissed" && <button type="button" onClick={() => update.mutate({ id: gap.id, status: "dismissed" })}>Dismiss</button>}{gap.status !== "open" && <button type="button" onClick={() => update.mutate({ id: gap.id, status: "open" })}>Reopen</button>}</div></article>)}</div>
  </main>;
}
