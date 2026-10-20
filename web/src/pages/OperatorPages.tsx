import { useEffect, useMemo, useRef, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, unwrap, unwrapNoContent, type Schemas } from "../api/client";
import { ApiError, errorMessage } from "../api/errors";
import { useAuth } from "../auth/AuthProvider";
import { CallRecordingPlayer } from "../components/CallRecordingPlayer";
import { ChannelIcon } from "../components/ChannelIcon";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Badge, DeleteIcon, EmptyState, ErrorState, LoadingState, PageHeader } from "../components/States";
import { formatDate } from "../lib/date";
import { transcriptRows } from "../lib/transcript";

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

function formatBookingStart(value: unknown, timezone: unknown) {
  if (typeof value !== "string") return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  try {
    return new Intl.DateTimeFormat("en-GB", {
      dateStyle: "full",
      timeStyle: "short",
      timeZone: typeof timezone === "string" ? timezone : "UTC",
    }).format(date);
  } catch {
    return null;
  }
}

function conversationQuery(filters: { channel: string; status: string; reviewOnly: boolean }, cursor?: string | null) {
  return {
    channel: channels.find((channel) => channel === filters.channel),
    status: statuses.find((status) => status === filters.status),
    has_review: filters.reviewOnly ? true : undefined,
    cursor: cursor ?? undefined,
    limit: 20,
  };
}

type ConversationSummary = Schemas["ConversationSummary"];

function localDateKey(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

export function groupConversationDays(rows: ConversationSummary[], now = new Date()) {
  const todayKey = localDateKey(now);
  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  yesterday.setDate(yesterday.getDate() - 1);
  const yesterdayKey = localDateKey(yesterday);
  const groups = new Map<string, { key: string; label: string; items: ConversationSummary[] }>();
  for (const row of rows) {
    const date = new Date(row.started_at);
    const key = Number.isNaN(date.getTime()) ? "unknown" : localDateKey(date);
    const label = key === todayKey
      ? "Today"
      : key === yesterdayKey
        ? "Yesterday"
        : key === "unknown"
          ? "Unknown date"
          : new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric" }).format(date);
    const group = groups.get(key) ?? { key, label, items: [] };
    group.items.push(row);
    groups.set(key, group);
  }
  return [...groups.values()];
}

export function formatConversationDuration(channel: string, startedAt: string, endedAt: string | null) {
  if (!["phone", "web"].includes(channel) || !endedAt) return null;
  const start = new Date(startedAt).getTime();
  const end = new Date(endedAt).getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
  const elapsed = (end - start) / 1000;
  if (elapsed < 1) return null;
  const seconds = Math.floor(elapsed);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function requestErrorMessage(error: unknown, fallback: string) {
  if (error instanceof ApiError) return errorMessage(error.body, error.message);
  return error instanceof Error ? error.message : fallback;
}

function conversationDeleteText(count: number) {
  return `${count === 1 ? "Its transcript and recording are" : "Their transcripts and recordings are"} permanently deleted. Contacts are kept. This can't be undone.`;
}

export function InboxPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { tenant, user, memberships, selectedTenant } = useAuth();
  const role = memberships.find(({ id }) => id === selectedTenant)?.role ?? tenant?.role;
  const canDelete = Boolean(user) && role === "owner";
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [pendingDelete, setPendingDelete] = useState<{ ids: string[]; bulk: boolean } | null>(null);
  const [deleteError, setDeleteError] = useState("");
  const [deleteStatus, setDeleteStatus] = useState("");
  const [search, setSearch] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const selectAllRef = useRef<HTMLInputElement | null>(null);
  const filterWrapRef = useRef<HTMLDivElement | null>(null);
  const filterButtonRef = useRef<HTMLButtonElement | null>(null);
  const channel = searchParams.get("channel") ?? "all";
  const status = searchParams.get("status") ?? "all";
  const reviewOnly = searchParams.get("has_review") === "true";
  const selectedConversationId = searchParams.get("id") ?? "";
  const filters = { channel, status, reviewOnly };
  useEffect(() => {
    if (!filtersOpen) return;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (event.target instanceof Node && !filterWrapRef.current?.contains(event.target)) setFiltersOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setFiltersOpen(false);
      filterButtonRef.current?.focus();
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsidePointer);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [filtersOpen]);
  useEffect(() => {
    setSelectedIds([]);
  }, [channel, status, reviewOnly]);
  const conversations = useInfiniteQuery({
    queryKey: ["conversations", filters],
    queryFn: ({ pageParam }) => unwrap(api.GET("/conversations", { params: { query: conversationQuery(filters, pageParam) } })),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
  });
  const pages = conversations.data?.pages;
  const rows = useMemo(() => pages?.flatMap(({ items }) => items) ?? [], [pages]);
  const visibleRows = useMemo(() => {
    const term = search.trim().toLocaleLowerCase();
    if (!term) return rows;
    return rows.filter((row) => [row.summary, row.contact_name, row.external_ref]
      .some((value) => value?.toLocaleLowerCase().includes(term)));
  }, [rows, search]);
  const groups = useMemo(() => groupConversationDays(visibleRows), [visibleRows]);
  const selectedVisibleCount = rows.filter(({ id }) => selectedIds.includes(id)).length;
  const allVisibleSelected = rows.length > 0 && selectedVisibleCount === rows.length;
  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate =
        selectedVisibleCount > 0 && selectedVisibleCount < rows.length;
    }
  }, [rows.length, selectedVisibleCount]);
  const deleteConversations = useMutation({
    mutationFn: async (request: { ids: string[]; bulk: boolean }) => {
      if (request.bulk) {
        return unwrap(api.POST("/conversations/bulk-delete", {
          body: { ids: request.ids },
        }));
      }
      await unwrapNoContent(api.DELETE("/conversations/{conversation_id}", {
        params: { path: { conversation_id: request.ids[0] } },
      }));
      return { deleted: 1, skipped_in_progress: 0 };
    },
    onSuccess: async (result, request) => {
      setPendingDelete(null);
      setSelectedIds([]);
      const skipped = result.skipped_in_progress ?? 0;
      setDeleteStatus(skipped
        ? `${skipped} conversation${skipped === 1 ? "" : "s"} skipped because they are still in progress.`
        : "");
      if (request.ids.includes(selectedConversationId)) {
        setSearchParams((current) => {
          const next = new URLSearchParams(current);
          next.delete("id");
          return next;
        });
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
        queryClient.invalidateQueries({ queryKey: ["conversation"] }),
        queryClient.invalidateQueries({ queryKey: ["assistants"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] }),
        queryClient.invalidateQueries({ queryKey: ["contacts"] }),
        queryClient.invalidateQueries({ queryKey: ["tasks"] }),
      ]);
    },
    onError: (error) => setDeleteError(
      requestErrorMessage(error, "Unable to delete conversation."),
    ),
  });
  const openConversation = (id: string) => {
    const isMobile = typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(max-width: 960px)").matches;
    if (isMobile) {
      navigate(`/conversations/${id}`);
      return;
    }
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("id", id);
      return next;
    });
  };
  function updateParam(key: string, value: string) {
    setSelectedIds([]);
    setDeleteStatus("");
    const next = new URLSearchParams(searchParams);
    if (!value || value === "all" || (key === "has_review" && value !== "true")) next.delete(key);
    else next.set(key, value);
    setSearchParams(next);
  }
  return <main className="page inbox-page" id="main-content">
    <PageHeader title="Inbox" lede="Every conversation your assistants handled, across phone, web, email and WhatsApp." />
    <div className="inbox-toolbar">
      <div className="inbox-search-field">
        <label className="sr-only" htmlFor="inbox-search">Search conversations</label>
        <input id="inbox-search" type="search" placeholder="Search conversations" value={search} onChange={(event) => setSearch(event.target.value)} />
      </div>
      <div className="inbox-filter-wrap" ref={filterWrapRef}>
        <button ref={filterButtonRef} type="button" className="button-secondary inbox-filter-button" aria-expanded={filtersOpen} aria-controls="inbox-filter-popover" onClick={() => setFiltersOpen((open) => !open)}>
          {icon("M4 5h16M7 12h10m-7 7h4")} Filters
        </button>
        {filtersOpen && <div className="inbox-filter-popover" id="inbox-filter-popover">
          <div className="field"><label htmlFor="filter-channel">Channel</label><select id="filter-channel" value={channel} onChange={(event) => updateParam("channel", event.target.value)}><option value="all">All channels</option>{channels.map((value) => <option key={value} value={value}>{value}</option>)}</select></div>
          <div className="field"><label htmlFor="filter-status">Status</label><select id="filter-status" value={status} onChange={(event) => updateParam("status", event.target.value)}><option value="all">All statuses</option>{statuses.map((value) => <option key={value} value={value}>{value}</option>)}</select></div>
          <label className="check-row"><input type="checkbox" checked={reviewOnly} onChange={(event) => updateParam("has_review", event.target.checked ? "true" : "false")} />Needs review only</label>
        </div>}
      </div>
      {canDelete && selectedIds.length > 0 && <button
        type="button"
        className="button-danger"
        onClick={() => {
          setDeleteError("");
          setPendingDelete({ ids: selectedIds, bulk: true });
        }}
      ><DeleteIcon /> Delete selected ({selectedIds.length})</button>}
    </div>
    {deleteStatus && <p className="muted" role="status">{deleteStatus}</p>}
    <div className={`inbox-split${selectedIds.length ? " inbox-has-selection" : ""}`}>
      <section className="inbox-list-panel" aria-label="Conversation list">
        <div className="inbox-list-header">
          {canDelete
            ? <label className="inbox-select-all"><input
              ref={selectAllRef}
              type="checkbox"
              aria-label="Select all conversations"
              checked={allVisibleSelected}
              onChange={(event) => setSelectedIds((current) => event.target.checked
                ? [...new Set([...current, ...rows.map(({ id }) => id)])]
                : current.filter((id) => !rows.some((row) => row.id === id)))}
            />Select all loaded</label>
            : <span className="inbox-list-heading">Conversations</span>}
        </div>
        <div className="inbox-list-scroll">
          {conversations.isLoading && <LoadingState />}
          {conversations.error && <ErrorState message="Conversations could not be loaded." />}
          {!conversations.isLoading && !visibleRows.length && <EmptyState>No conversations match these filters.</EmptyState>}
          {groups.map((group) => <section className="inbox-day-group" key={group.key}>
            <h2 className="inbox-day-heading"><span>{group.label}</span><span>{group.items.length}</span></h2>
            {group.items.map((conversation) => {
              const contactName = conversation.contact_name ?? conversation.external_ref;
              const duration = formatConversationDuration(conversation.channel, conversation.started_at, conversation.ended_at);
              const startedAt = new Date(conversation.started_at);
              const time = Number.isNaN(startedAt.getTime())
                ? ""
                : new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" }).format(startedAt);
              return <article className={`inbox-row${canDelete ? " inbox-row-owner" : ""}${conversation.id === selectedConversationId ? " inbox-row-selected" : ""}`} key={conversation.id} onClick={(event) => {
                if (event.target instanceof HTMLElement && event.target.closest("a, input, button")) return;
                openConversation(conversation.id);
              }}>
                {canDelete && <input
                  className="inbox-row-checkbox"
                  type="checkbox"
                  aria-label={`Select conversation ${conversation.summary ?? conversation.external_ref}`}
                  checked={selectedIds.includes(conversation.id)}
                  onChange={(event) => setSelectedIds((current) => event.target.checked
                    ? [...current, conversation.id]
                    : current.filter((id) => id !== conversation.id))}
                />}
                <div className="inbox-row-content">
                  <div className="inbox-row-title">
                    <span className="inbox-row-contact">{contactName}</span>
                    <time dateTime={conversation.started_at}>{time}</time>
                  </div>
                  <div className="inbox-row-meta">
                    <span className="inbox-channel"><ChannelIcon kind={conversation.channel} /></span>
                    <span>{channelLabel(conversation.channel)}{duration && <> · {duration}</>}</span>
                    {conversation.test_call && <Badge tone="neutral">Test call</Badge>}
                    {conversation.needs_review && <Badge tone="warning">Needs review</Badge>}
                    {["active", "failed"].includes(conversation.status) && <Badge tone={statusTone(conversation.status)}>{conversation.status === "active" ? "Active" : "Failed"}</Badge>}
                  </div>
                  <div className="inbox-row-snippet">
                    <Link
                      to={`/conversations/${conversation.id}`}
                      aria-current={conversation.id === selectedConversationId ? "page" : undefined}
                      onClick={(event) => {
                        if (typeof window === "undefined" || typeof window.matchMedia !== "function" ||
                          !window.matchMedia("(max-width: 960px)").matches) {
                          event.preventDefault();
                          openConversation(conversation.id);
                        }
                      }}
                    >{conversation.summary ?? "No summary"}</Link>
                  </div>
                </div>
              </article>;
            })}
          </section>)}
        </div>
        <div className="inbox-list-footer">
          <span>{visibleRows.length} results</span>
          {conversations.hasNextPage && <button type="button" className="button-ghost button-sm" onClick={() => void conversations.fetchNextPage()} disabled={conversations.isFetchingNextPage}>{conversations.isFetchingNextPage ? "Loading…" : "Load more"}</button>}
        </div>
      </section>
      <section className="inbox-detail-panel" aria-label="Selected conversation">
        {selectedConversationId
          ? <ConversationDetail
            id={selectedConversationId}
            embedded
            onDeleted={() => {
              setSearchParams((current) => {
                const next = new URLSearchParams(current);
                next.delete("id");
                return next;
              });
            }}
          />
          : <EmptyState>Select a conversation to view its details.</EmptyState>}
      </section>
    </div>
    {pendingDelete && <ConfirmDialog
      title={`Delete ${pendingDelete.ids.length} conversation${pendingDelete.ids.length === 1 ? "" : "s"}?`}
      confirmLabel="Delete"
      tone="danger"
      onCancel={() => {
        setPendingDelete(null);
        setDeleteError("");
      }}
      onConfirm={() => deleteConversations.mutate(pendingDelete)}
    >
      <p>{conversationDeleteText(pendingDelete.ids.length)}</p>
      {deleteError && <p role="alert">{deleteError}</p>}
    </ConfirmDialog>}
  </main>;
}

const icon = (path: string) => <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d={path} /></svg>;

const channelLabel = (channel: string) => channel === "whatsapp" ? "WhatsApp" : channel === "gmail" ? "Email" : channel === "web" ? "Web call" : "Phone";

function statusTone(status: string): "neutral" | "success" | "warning" | "danger" | "info" {
  if (["completed", "done", "answered"].includes(status)) return "success";
  if (["failed"].includes(status)) return "danger";
  if (["active", "open"].includes(status)) return "info";
  return "neutral";
}

export function ConversationPage() {
  const { id = "" } = useParams();
  return <ConversationDetail id={id} />;
}

function ConversationDetail({ id, embedded = false, onDeleted }: {
  id: string;
  embedded?: boolean;
  onDeleted?: () => void;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { tenant, user, memberships, selectedTenant } = useAuth();
  const role = memberships.find(({ id: tenantId }) => tenantId === selectedTenant)?.role ?? tenant?.role;
  const canDelete = Boolean(user) && role === "owner";
  const [openReview, setOpenReview] = useState("");
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const detailQuery = useQuery({ queryKey: ["conversation", id], queryFn: () => unwrap(api.GET("/conversations/{conversation_id}", { params: { path: { conversation_id: id } } })) });
  const assistantId = detailQuery.data?.assistant_id ?? "";
  const assistantQuery = useQuery({ queryKey: ["assistant", assistantId], queryFn: () => unwrap(api.GET("/assistants/{assistant_id}", { params: { path: { assistant_id: assistantId } } })), enabled: Boolean(assistantId) });
  const detail = detailQuery.data;
  const bookingValue = detail?.metrics.booking;
  const booking = bookingValue && typeof bookingValue === "object"
    ? bookingValue as Record<string, unknown>
    : null;
  const bookingStart = booking ? formatBookingStart(booking.start, booking.timezone) : null;
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
    mutationFn: (input: Schemas["ConversationReviewCreate"]) => unwrap(api.POST("/conversations/{conversation_id}/reviews", { params: { path: { conversation_id: id } }, body: input })),
    onSuccess: async () => {
      setOpenReview("");
      await queryClient.invalidateQueries({ queryKey: ["conversation", id] });
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      await queryClient.invalidateQueries({ queryKey: ["conversations-needing-review"] });
    },
  });
  const patchTask = useMutation({
    mutationFn: (input: { id: string; status: Schemas["TaskUpdate"]["status"] }) => unwrap(api.PATCH("/tasks/{task_id}", { params: { path: { task_id: input.id } }, body: { status: input.status } })),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ["conversation", id] }); void queryClient.invalidateQueries({ queryKey: ["tasks"] }); },
  });
  const deleteConversation = useMutation({
    mutationFn: () => unwrapNoContent(api.DELETE("/conversations/{conversation_id}", {
      params: { path: { conversation_id: id } },
    })),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
        queryClient.invalidateQueries({ queryKey: ["conversation"] }),
        queryClient.invalidateQueries({ queryKey: ["assistants"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] }),
        queryClient.invalidateQueries({ queryKey: ["contacts"] }),
        queryClient.invalidateQueries({ queryKey: ["tasks"] }),
      ]);
      if (onDeleted) onDeleted();
      else navigate("/inbox");
    },
    onError: (error) => setDeleteError(
      requestErrorMessage(error, "Unable to delete conversation."),
    ),
  });
  if (detailQuery.isLoading) return embedded
    ? <div className="conversation-detail-state"><LoadingState /></div>
    : <main className="page" id="main-content"><LoadingState /></main>;
  if (detailQuery.error || !detail) return embedded
    ? <div className="conversation-detail-state"><ErrorState message="Conversation could not be loaded." /></div>
    : <main className="page" id="main-content"><ErrorState message="Conversation could not be loaded." /></main>;
  const emailTriage = asRecord(detail.metrics.email_triage);
  const transcript = transcriptRows(detail.messages, detail.channel, detail.started_at);
  const messageLayout = detail.channel !== "phone" && detail.channel !== "web";
  const channelName = channelLabel(detail.channel);
  const detailDuration = formatConversationDuration(detail.channel, detail.started_at, detail.ended_at);
  const titleName = detail.contact_name ?? detail.external_ref;
  const badges = <div className="conversation-meta">
    <span className="conversation-meta-item"><ChannelIcon kind={detail.channel} /> {channelName}</span>
    <span className="conversation-meta-item">{formatDate(detail.started_at)}</span>
    {detailDuration && <span className="conversation-meta-item">{detailDuration}</span>}
    <span className="conversation-meta-item">{detail.person_id ? <Link to={`/contacts/${detail.person_id}`}>{titleName}</Link> : titleName}</span>
    <Badge tone={statusTone(detail.status)}>{detail.status}</Badge>
    {detail.test_call && <Badge tone="neutral">Test call</Badge>}
    {detail.end_reason && <span className="conversation-meta-item">Ended: {detail.end_reason}</span>}
  </div>;
  const exportTranscript = () => {
    const lines = [`${titleName} · ${channelName} · ${formatDate(detail.started_at)}`, detail.summary ? `Summary: ${detail.summary}` : "", ""];
    transcript.forEach((row) => lines.push(`[${row.time}] ${row.speaker}: ${row.text}`));
    const blob = new Blob([lines.join("\n")], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `transcript-${detail.started_at.slice(0, 10)}-${titleName.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}.txt`;
    anchor.click();
    URL.revokeObjectURL(url);
  };
  const exportButton = transcript.length > 0 && <button type="button" className={embedded ? "button-secondary button-sm" : "button-secondary"} aria-label={embedded ? "Export transcript" : undefined} title="Export transcript" onClick={exportTranscript}>{icon("M12 3v12m0 0-4-4m4 4 4-4M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2")}{!embedded && <> Export transcript</>}</button>;
  const deleteButton = canDelete && <button type="button" className={embedded ? "button-danger button-sm" : "button-danger"} aria-label={embedded ? "Delete conversation" : undefined} title={embedded ? "Delete conversation" : undefined} onClick={() => {
    setDeleteError("");
    setDeleteDialogOpen(true);
  }}>{embedded ? <DeleteIcon /> : <><DeleteIcon /> Delete conversation</>}</button>;
  const content = <>
    {deleteError && <p role="alert">{deleteError}</p>}
    {detail.summary && <div className="conversation-summary-strip">{icon("M12 3v3m0 12v3m9-9h-3M6 12H3m14.5-6.5-2 2m-7 7-2 2m0-11 2 2m7 7 2 2")}<span className="conversation-summary-label">Summary</span><p>{detail.summary}</p></div>}
    {booking && <section className="panel stack">
      <h2>Appointment booking</h2>
      <p><strong>{String(booking.label ?? booking.appointment_type ?? "Appointment")}</strong></p>
      <p>{String(booking.text ?? booking.speech ?? "Booking details unavailable.")}</p>
      <p>Status: {String(booking.status ?? "booked")}</p>
      {bookingStart && <p><strong>Start</strong>: {bookingStart} ({String(booking.timezone ?? "UTC")})</p>}
      {(typeof booking.attendee_name === "string" || typeof booking.attendee_email === "string") && <p>
        <strong>Attendee</strong>: {typeof booking.attendee_name === "string" ? booking.attendee_name : "—"}
        {typeof booking.attendee_email === "string" && <> · {booking.attendee_email}</>}
      </p>}
      {typeof booking.event_link === "string" && <p><a href={booking.event_link} target="_blank" rel="noreferrer">Open Calendar event</a></p>}
    </section>}
    {detail.channel === "gmail" && Object.keys(emailTriage).length > 0 && <section className="panel"><h2>Email triage</h2><div className="card-grid"><div><strong>Category</strong><p>{String(emailTriage.category ?? "—")}</p></div><div><strong>Reason</strong><p>{String(emailTriage.reason ?? "—")}</p></div></div></section>}
    {detail.transfer && <section className="panel"><h2>Transfer</h2><div className="card-grid"><div><strong>Target</strong><p>{detail.transfer.target_key}</p></div><div><strong>Status</strong><p>{detail.transfer.status}</p></div><div><strong>Latency</strong><p>{detail.transfer.latency_ms == null ? "—" : `${detail.transfer.latency_ms} ms`}</p></div></div></section>}
    <CallRecordingPlayer conversationId={id} recording={detail.recording} />
    <section className="panel conversation-history">
      <h2 className="eyebrow conversation-history-heading">History</h2>
      <ol className="call-transcript" data-layout={messageLayout ? "message" : undefined} aria-label="Conversation transcript">
        {transcript.map((row) => <li className="call-transcript-row" data-role={row.role} key={row.id}>
          {messageLayout ? <>
            <div className="call-transcript-meta">
              <strong className="call-transcript-speaker">{row.speaker}{row.draft && <Badge tone="neutral">Draft in Gmail</Badge>}</strong>
              <span className="call-transcript-date"><span className="call-transcript-separator" aria-hidden="true">·</span> <time className="call-transcript-time" dateTime={row.ts}>{row.time}</time></span>
            </div>
            <div className="call-transcript-text">{row.text}</div>
          </> : <>
            <time className="call-transcript-time" dateTime={row.ts}>{row.time}</time>
            <strong className="call-transcript-speaker">
              {row.speaker}
              {row.draft && <Badge tone="neutral">Draft in Gmail</Badge>}
            </strong>
            <div className="call-transcript-text">{row.text}</div>
          </>}
        </li>)}
      </ol>
    </section>
    <section className="panel stack"><h2>Intake comparison</h2>
      {!intakes.merged && <p className="muted">Merged intake is unavailable. Live and post-call values are shown where available.</p>}
      <div className="table-scroll">
        <table className="intake-table">
          <caption className="sr-only">Intake comparison by field</caption>
          <thead><tr><th scope="col">Field</th><th scope="col">Live</th><th scope="col">Post-call</th><th scope="col">Merged</th></tr></thead>
          <tbody>
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
          return <tr key={name}>
            <th scope="row"><strong>{fieldLabel}</strong>{isCritical(assistantQuery.data, mergedData, name) && <span className="muted"> · critical</span>}</th>
            <td>{displayValue(liveValue)}{live?.confirmed && <p><Badge tone="success">Confirmed by caller</Badge></p>}</td>
            <td>{displayValue(post?.value)}{typeof post?.confidence === "number" && <p className="muted">Confidence {post.confidence.toFixed(2)}</p>}</td>
            <td>
              {merged ? <><strong>{displayValue(merged.value)}</strong>{typeof merged.confidence === "number" && <p className="muted">Confidence {merged.confidence.toFixed(2)}</p>}<p><Badge>{mergedSource(merged)}</Badge></p>{merged.status === "conflict" && merged.alternative !== undefined && <p>Alternative: {displayValue(merged.alternative)}</p>}{flagged && <Badge tone="warning">Needs review</Badge>}{merged.status === "human" && merged.sources?.human?.note && <p>Note: {merged.sources.human.note}</p>}</> : <span>—</span>}
              <div><button type="button" onClick={() => setOpenReview(openReview === name ? "" : name)}>{openReview === name ? "Close review" : "Review field"}</button></div>
              {openReview === name && <ReviewForm field={name} candidates={candidates} live={live} post={post} merged={merged} onSubmit={(input) => review.mutate(input)} pending={review.isPending} />}
            </td>
          </tr>;
        })}
        {!fieldNames.length && <tr><td className="muted" colSpan={4}>No intake fields recorded.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
    <section className="panel"><h2>After-conversation actions</h2>{(detail.action_runs ?? []).length ? detail.action_runs?.map((run) => <article className="list-row stack" key={run.id}><strong>{run.action_key} · {run.action_kind}</strong><span>Status: {run.status} · attempts: {run.attempts} · fires: {run.fire_count}</span><span>Events: {run.fired_events.join(", ") || "—"}</span>{run.pending_events.length > 0 && <span>Pending: {run.pending_events.join(", ")}</span>}{run.last_error && <p role="alert">{run.last_error}</p>}<pre>{JSON.stringify(run.output, null, 2)}</pre></article>) : <p className="muted">No after-conversation actions have run.</p>}</section>
    <section className="panel"><h2>Tasks</h2>{detail.tasks.length ? detail.tasks.map((task) => <div className="list-row" key={task.id}><span>{task.kind} · {task.source} · {compactPayload(task.payload)}</span><label className="sr-only" htmlFor={`conversation-task-${task.id}`}>Status for {task.kind}</label><select id={`conversation-task-${task.id}`} value={task.status} onChange={(event) => patchTask.mutate({ id: task.id, status: event.target.value as Schemas["TaskUpdate"]["status"] })}><option value="open">open</option><option value="done">done</option><option value="dismissed">dismissed</option></select></div>) : <p className="muted">No tasks.</p>}</section>
    {deleteDialogOpen && <ConfirmDialog
      title="Delete 1 conversation?"
      confirmLabel="Delete"
      tone="danger"
      onCancel={() => {
        setDeleteDialogOpen(false);
        setDeleteError("");
      }}
      onConfirm={() => deleteConversation.mutate()}
    >
      <p>{conversationDeleteText(1)}</p>
    </ConfirmDialog>}
  </>;
  return embedded
    ? <div className="conversation-detail">
      <header className="conversation-detail-header">
        <div className="conversation-detail-title"><h2>{titleName}</h2>{badges}</div>
        <div className="button-row">{exportButton}{deleteButton}</div>
      </header>
      <div className="conversation-detail-scroll">{content}</div>
    </div>
    : <main className="page" id="main-content">
      <PageHeader title={titleName} lede={badges} actions={<div className="button-row">
        {exportButton}
        {deleteButton}
        <Link className="button" to="/inbox">Back to inbox</Link>
      </div>} />
      {content}
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

function useRowSelection(rowIds: string[]) {
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const selectAllRef = useRef<HTMLInputElement | null>(null);
  const selectedVisible = rowIds.filter((id) => selectedIds.includes(id)).length;
  const allSelected = rowIds.length > 0 && selectedVisible === rowIds.length;
  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = selectedVisible > 0 && selectedVisible < rowIds.length;
  }, [rowIds.length, selectedVisible]);
  return {
    selectedIds,
    selectAllRef,
    allSelected,
    clear: () => setSelectedIds([]),
    toggle: (id: string) => setSelectedIds((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id])),
    toggleAll: () => setSelectedIds(allSelected ? [] : rowIds),
  };
}

function useCanDelete() {
  const { tenant, user, memberships, selectedTenant } = useAuth();
  const role = memberships.find(({ id }) => id === selectedTenant)?.role ?? tenant?.role;
  return Boolean(user) && role === "owner";
}

export function ContactsPage() {
  const canDelete = useCanDelete();
  const queryClient = useQueryClient();
  const contacts = useInfiniteQuery({
    queryKey: ["contacts"],
    queryFn: ({ pageParam }) => unwrap(api.GET("/contacts", { params: { query: { cursor: pageParam ?? undefined, limit: 50 } } })),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
  });
  const [filter, setFilter] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const rows = useMemo(() => (contacts.data?.pages.flatMap(({ items }) => items) ?? []).filter(({ display_name }) => display_name.toLowerCase().includes(filter.toLowerCase())), [contacts.data, filter]);
  const rowIds = useMemo(() => rows.map(({ id }) => id), [rows]);
  const selection = useRowSelection(rowIds);
  const remove = useMutation({
    mutationFn: (ids: string[]) => unwrap(api.POST("/contacts/bulk-delete", { body: { ids } })),
    onSuccess: async () => {
      setConfirmOpen(false);
      selection.clear();
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["contacts"] }),
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
        queryClient.invalidateQueries({ queryKey: ["tasks"] }),
      ]);
    },
    onError: (error) => setDeleteError(requestErrorMessage(error, "Unable to delete contacts.")),
  });
  const count = selection.selectedIds.length;
  return <main className="page" id="main-content"><PageHeader title="Contacts" lede="Callers and senders your assistants have identified." />
    <div className="toolbar">
      <div className="field"><label htmlFor="contact-filter">Filter by name</label><input id="contact-filter" value={filter} onChange={(event) => setFilter(event.target.value)} /></div>
      {canDelete && count > 0 && <button type="button" className="button-danger" onClick={() => { setDeleteError(""); setConfirmOpen(true); }}><DeleteIcon /> Delete selected ({count})</button>}
    </div>
    {contacts.isLoading && <LoadingState />}{contacts.error && <ErrorState message="Contacts could not be loaded." />}
    {!rows.length && !contacts.isLoading && <EmptyState>No contacts found.</EmptyState>}
    <div className="table-wrap"><table><thead><tr>
      {canDelete && <th className="select-cell"><input ref={selection.selectAllRef} type="checkbox" aria-label="Select all contacts" checked={selection.allSelected} onChange={selection.toggleAll} /></th>}
      <th>Name</th><th>Created</th><th>Updated</th><th>Status</th></tr></thead><tbody>{rows.map((contact) => <tr key={contact.id} aria-selected={canDelete ? selection.selectedIds.includes(contact.id) : undefined}>
      {canDelete && <td className="select-cell"><input type="checkbox" aria-label={`Select ${contact.display_name}`} checked={selection.selectedIds.includes(contact.id)} onChange={() => selection.toggle(contact.id)} /></td>}
      <td><Link to={`/contacts/${contact.id}`}>{contact.display_name}</Link></td><td className="cell-muted">{formatDate(contact.created_at)}</td><td className="cell-muted">{formatDate(contact.updated_at)}</td><td>{contact.merged_into_id ? <Badge>merged</Badge> : <Badge tone="success">Active</Badge>}</td></tr>)}</tbody></table></div>
    {contacts.hasNextPage && <button type="button" onClick={() => void contacts.fetchNextPage()} disabled={contacts.isFetchingNextPage}>Load more</button>}
    {confirmOpen && <ConfirmDialog
      title={`Delete ${count} contact${count === 1 ? "" : "s"}?`}
      confirmLabel="Delete"
      tone="danger"
      onCancel={() => { setConfirmOpen(false); setDeleteError(""); }}
      onConfirm={() => remove.mutate(selection.selectedIds)}
    >
      <p>Their conversations and tasks are kept, without a contact. This can't be undone.</p>
      {deleteError && <p role="alert">{deleteError}</p>}
    </ConfirmDialog>}
  </main>;
}

export function ContactPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [otherId, setOtherId] = useState("");
  const [dialog, setDialog] = useState<"merge" | "split" | "">("");
  const [selectedIdentities, setSelectedIdentities] = useState<string[]>([]);
  const detailQuery = useQuery({ queryKey: ["contact", id], queryFn: () => unwrap(api.GET("/contacts/{contact_id}", { params: { path: { contact_id: id } } })) });
  const contactsQuery = useQuery({ queryKey: ["contacts-for-merge"], queryFn: () => unwrap(api.GET("/contacts", { params: { query: { limit: 100 } } })) });
  const detail = detailQuery.data;
  const merge = useMutation({
    mutationFn: () => unwrap(api.POST("/contacts/{contact_id}/merge", { params: { path: { contact_id: id } }, body: { other_id: otherId } })),
    onSuccess: async ({ survivor_id }) => {
      setDialog("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["contacts"] }),
        queryClient.invalidateQueries({ queryKey: ["contacts-for-merge"] }),
        queryClient.invalidateQueries({ queryKey: ["contact", survivor_id] }),
        queryClient.invalidateQueries({ queryKey: ["contact", id] }),
      ]);
      navigate(`/contacts/${survivor_id}`);
    },
  });
  const split = useMutation({
    mutationFn: () => unwrap(api.POST("/contacts/{contact_id}/split", { params: { path: { contact_id: id } }, body: { identity_ids: selectedIdentities } })),
    onSuccess: async ({ split_person_id }) => {
      setDialog("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["contacts"] }),
        queryClient.invalidateQueries({ queryKey: ["contacts-for-merge"] }),
        queryClient.invalidateQueries({ queryKey: ["contact", split_person_id] }),
        queryClient.invalidateQueries({ queryKey: ["contact", id] }),
      ]);
      navigate(`/contacts/${split_person_id}`);
    },
  });
  if (detailQuery.isLoading) return <main className="page" id="main-content"><LoadingState /></main>;
  if (detailQuery.error || !detail) return <main className="page" id="main-content"><ErrorState message="Contact could not be loaded." /></main>;
  const mergeChoices = (contactsQuery.data?.items ?? []).filter((contact) => contact.id !== id && !contact.merged_into_id);
  return <main className="page" id="main-content"><PageHeader title={detail.display_name} actions={<Link className="button" to="/contacts">All contacts</Link>} />
    <div className="toolbar"><div className="field"><label htmlFor="merge-contact">Merge another contact into this one</label><select id="merge-contact" value={otherId} onChange={(event) => setOtherId(event.target.value)}><option value="">Choose a contact</option>{mergeChoices.map((contact) => <option key={contact.id} value={contact.id}>{contact.display_name}</option>)}</select></div><button type="button" disabled={!otherId} onClick={() => setDialog("merge")}>Merge contact</button></div>
    <section className="panel"><h2>Identities</h2>{detail.identities.map((identity) => <div className="list-row" key={identity.id}><label className="check-row"><input type="checkbox" checked={selectedIdentities.includes(identity.id)} onChange={(event) => setSelectedIdentities((current) => event.target.checked ? [...current, identity.id] : current.filter((item) => item !== identity.id))} />{identity.type}: {identity.value_normalized}</label><span>{identity.verified ? "Verified" : "Unverified"} · {identity.source} · {identity.confidence.toFixed(2)}</span></div>)}{!detail.identities.length && <p className="muted">No identities yet.</p>}<button type="button" disabled={!selectedIdentities.length} onClick={() => setDialog("split")}>Split selected identities</button></section>
    <section className="panel"><h2>Active facts</h2>{detail.active_facts.map((fact) => <div className="list-row" key={fact.id}><span><strong>{fact.predicate}</strong>: {displayValue(fact.value)}<br /><span className="muted">{fact.evidence} · confidence {fact.confidence.toFixed(2)} · since {formatDate(fact.valid_from)}</span></span></div>)}{!detail.active_facts.length && <p className="muted">No facts yet.</p>}</section>
    <section className="panel"><h2>Conversations</h2>{detail.conversations.map((conversation) => <div className="list-row" key={conversation.id}><Link to={`/conversations/${conversation.id}`}>{conversation.channel} · {conversation.summary ?? conversation.external_ref}</Link><span>{formatDate(conversation.started_at)}</span></div>)}{!detail.conversations.length && <p className="muted">No conversations yet.</p>}</section>
    <section className="panel"><h2>Open tasks</h2>{detail.open_tasks.map((task) => <p key={task.id}>{task.kind} · {task.status} · {compactPayload(task.payload)}</p>)}{!detail.open_tasks.length && <p className="muted">No open tasks.</p>}</section>
    <section className="panel"><h2>Open commitments</h2>{detail.open_commitments.map((commitment) => <p key={commitment.id}>{commitment.what} · {commitment.due_at ? formatDate(commitment.due_at) : "No due date"} · {commitment.status}</p>)}{!detail.open_commitments.length && <p className="muted">No open commitments.</p>}</section>
    <section className="panel"><h2>Context card preview</h2>{detail.context_card ? <pre className="context-card">{detail.context_card}</pre> : <p className="muted">No context card yet.</p>}</section>
    {dialog === "merge" && <ConfirmDialog title={`Merge ${mergeChoices.find(({ id: other }) => other === otherId)?.display_name ?? "contact"} into ${detail.display_name}?`} onCancel={() => setDialog("")} onConfirm={() => merge.mutate()} confirmLabel="Merge">Identities, facts and conversations move to {detail.display_name}. This is audited and can be reversed by a split.</ConfirmDialog>}
    {dialog === "split" && <ConfirmDialog title="Split selected identities?" onCancel={() => setDialog("")} onConfirm={() => split.mutate()} confirmLabel="Split">The selected identities will move to a new contact profile.</ConfirmDialog>}
  </main>;
}

export function TasksPage() {
  const canDelete = useCanDelete();
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState("all");
  const [kindFilter, setKindFilter] = useState("all");
  const [assistantFilter, setAssistantFilter] = useState("all");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const assistants = useQuery({ queryKey: ["assistants"], queryFn: () => unwrap(api.GET("/assistants")) });
  const tasks = useQuery({
    queryKey: ["tasks", statusFilter, kindFilter, assistantFilter],
    queryFn: () => unwrap(api.GET("/tasks", { params: { query: {
      status: statusFilter === "all" ? undefined : statusFilter,
      kind: kindFilter === "all" ? undefined : kindFilter,
      assistant_id: assistantFilter === "all" ? undefined : assistantFilter,
    } } })),
  });
  const update = useMutation({
    mutationFn: (input: { id: string; status: Schemas["TaskUpdate"]["status"] }) => unwrap(api.PATCH("/tasks/{task_id}", { params: { path: { task_id: input.id } }, body: { status: input.status } })),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ["tasks"] }); },
  });
  const rows = useMemo(() => tasks.data?.items ?? [], [tasks.data]);
  const rowIds = useMemo(() => rows.map(({ id }) => id), [rows]);
  const selection = useRowSelection(rowIds);
  const remove = useMutation({
    mutationFn: (ids: string[]) => unwrap(api.POST("/tasks/bulk-delete", { body: { ids } })),
    onSuccess: async () => {
      setConfirmOpen(false);
      selection.clear();
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["tasks"] }),
        queryClient.invalidateQueries({ queryKey: ["conversation"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] }),
      ]);
    },
    onError: (error) => setDeleteError(requestErrorMessage(error, "Unable to delete tasks.")),
  });
  const count = selection.selectedIds.length;
  return <main className="page" id="main-content"><PageHeader title="Tasks" lede="Callbacks, bookings and other follow-ups created from conversations." />
    <div className="toolbar"><div className="field"><label htmlFor="task-status-filter">Status</label><select id="task-status-filter" value={statusFilter} onChange={(event) => { setStatusFilter(event.target.value); selection.clear(); }}><option value="all">All statuses</option>{["open", "done", "dismissed"].map((status) => <option key={status}>{status}</option>)}</select></div><div className="field"><label htmlFor="task-kind-filter">Kind</label><input id="task-kind-filter" value={kindFilter === "all" ? "" : kindFilter} onChange={(event) => { setKindFilter(event.target.value || "all"); selection.clear(); }} placeholder="All kinds" /></div><div className="field"><label htmlFor="task-assistant-filter">Assistant</label><select id="task-assistant-filter" value={assistantFilter} onChange={(event) => { setAssistantFilter(event.target.value); selection.clear(); }}><option value="all">All assistants</option>{assistants.data?.items.map((assistant) => <option key={assistant.id} value={assistant.id}>{assistant.name}</option>)}</select></div>
      {canDelete && count > 0 && <button type="button" className="button-danger" onClick={() => { setDeleteError(""); setConfirmOpen(true); }}><DeleteIcon /> Delete selected ({count})</button>}
    </div>
    {tasks.isLoading && <LoadingState />}{tasks.error && <ErrorState message="Tasks could not be loaded." />}{!rows.length && !tasks.isLoading && !tasks.error && <EmptyState>No tasks match these filters.</EmptyState>}
    <div className="table-wrap"><table><thead><tr>
      {canDelete && <th className="select-cell"><input ref={selection.selectAllRef} type="checkbox" aria-label="Select all tasks" checked={selection.allSelected} onChange={selection.toggleAll} /></th>}
      <th>Kind</th><th>Status</th><th>Source</th><th>Due</th><th>Details</th><th>Update status</th></tr></thead><tbody>{rows.map((task) => <tr key={task.id} aria-selected={canDelete ? selection.selectedIds.includes(task.id) : undefined}>
      {canDelete && <td className="select-cell"><input type="checkbox" aria-label={`Select ${task.kind} task`} checked={selection.selectedIds.includes(task.id)} onChange={() => selection.toggle(task.id)} /></td>}
      <td className="cell-primary">{task.kind}</td><td><Badge tone={statusTone(task.status)}>{task.status}</Badge></td><td>{task.source}</td><td className="cell-muted">{formatDate(task.due_at)}</td><td>{compactPayload(task.payload)}</td><td><label className="sr-only" htmlFor={`task-status-${task.id}`}>Status for {task.kind}</label><select id={`task-status-${task.id}`} value={task.status} onChange={(event) => update.mutate({ id: task.id, status: event.target.value as Schemas["TaskUpdate"]["status"] })}><option value="open">open</option><option value="done">done</option><option value="dismissed">dismissed</option></select></td></tr>)}</tbody></table></div>
    {confirmOpen && <ConfirmDialog
      title={`Delete ${count} task${count === 1 ? "" : "s"}?`}
      confirmLabel="Delete"
      tone="danger"
      onCancel={() => { setConfirmOpen(false); setDeleteError(""); }}
      onConfirm={() => remove.mutate(selection.selectedIds)}
    >
      <p>This can't be undone.</p>
      {deleteError && <p role="alert">{deleteError}</p>}
    </ConfirmDialog>}
  </main>;
}

export function KnowledgeGapsPage() {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState("open");
  const [assistantFilter, setAssistantFilter] = useState("all");
  const assistants = useQuery({ queryKey: ["assistants"], queryFn: () => unwrap(api.GET("/assistants")) });
  const gaps = useQuery({
    queryKey: ["knowledge-gaps", filter, assistantFilter],
    queryFn: () => unwrap(api.GET("/knowledge-gaps", { params: { query: {
      status: filter === "all" ? undefined : filter,
      assistant_id: assistantFilter === "all" ? undefined : assistantFilter,
    } } })),
  });
  const update = useMutation({
    mutationFn: (input: { id: string; status: Schemas["KnowledgeGapUpdate"]["status"] }) => unwrap(api.PATCH("/knowledge-gaps/{gap_id}", { params: { path: { gap_id: input.id } }, body: { status: input.status } })),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ["knowledge-gaps"] }); },
  });
  const rows = gaps.data?.items ?? [];
  return <main className="page" id="main-content"><PageHeader title="Knowledge gaps" lede="Questions your assistants could not answer. Add the answer to your knowledge to close the gap." />
    <div className="toolbar"><div className="field"><label htmlFor="gap-filter">Status</label><select id="gap-filter" value={filter} onChange={(event) => setFilter(event.target.value)}><option value="all">All statuses</option><option value="open">Open</option><option value="answered">Answered</option><option value="dismissed">Dismissed</option></select></div><div className="field"><label htmlFor="gap-assistant-filter">Assistant</label><select id="gap-assistant-filter" value={assistantFilter} onChange={(event) => setAssistantFilter(event.target.value)}><option value="all">All assistants</option>{assistants.data?.items.map((assistant) => <option key={assistant.id} value={assistant.id}>{assistant.name}</option>)}</select></div></div>
    {gaps.isLoading && <LoadingState />}{gaps.error && <ErrorState message="Knowledge gaps could not be loaded." />}{!rows.length && !gaps.isLoading && !gaps.error && <EmptyState>No knowledge gaps match this status.</EmptyState>}
    <div className="card-grid">{rows.map((gap) => <article className="panel stack" key={gap.id}><div className="button-row"><Badge tone={gap.status === "open" ? "warning" : "success"}>{gap.status}</Badge><span className="muted">{formatDate(gap.created_at)}</span></div><h2>{gap.question}</h2><p>{gap.evidence}</p><Link to={`/conversations/${gap.conversation_id}`}>View conversation</Link><div className="button-row">{gap.status !== "answered" && <button type="button" onClick={() => update.mutate({ id: gap.id, status: "answered" })}>Mark answered</button>}{gap.status !== "dismissed" && <button type="button" onClick={() => update.mutate({ id: gap.id, status: "dismissed" })}>Dismiss</button>}{gap.status !== "open" && <button type="button" onClick={() => update.mutate({ id: gap.id, status: "open" })}>Reopen</button>}</div></article>)}</div>
  </main>;
}
