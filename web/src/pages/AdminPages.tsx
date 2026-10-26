import { Fragment, useEffect, useMemo, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, NavLink, useNavigate, useParams, useSearchParams } from "react-router-dom";
import type {
  AdminConversationItem,
  EvalCaseResponse,
  EvalRunResponse,
  EvalRunSummary,
} from "../api/adminApi";
import {
  createEvalCase,
  getAdminConversation,
  getAdminOverview,
  getEvalRun,
  listAdminConversations,
  listEvalCases,
  listEvalRuns,
  reviewConversations,
  rescoreConversation,
} from "../api/adminApi";
import { Badge, EmptyState, ErrorState, LoadingState, PageHeader } from "../components/States";
import { AdminQualityChart } from "../components/AdminQualityChart";
import dashboardStyles from "../components/Dashboard.module.css";
import styles from "../components/Admin.module.css";
import { MoreMenu } from "../components/MoreMenu";
import { channelLabels } from "../lib/dashboard";
import { transcriptRows } from "../lib/transcript";
import {
  countCaseChecks,
  readObjective,
  readRunResults,
  readRunSummary,
  scoreTone,
  type AdminObjective,
} from "../lib/admin";

function channelLabel(channel: string) {
  return channelLabels[channel as keyof typeof channelLabels] ?? channel;
}

function AdminSubnav() {
  return <nav className="subnav" aria-label="Admin navigation">
    <NavLink to="/admin" end>Overview</NavLink>
    <NavLink to="/admin/evals">Evals</NavLink>
  </nav>;
}

function updateParams(setSearchParams: (params: URLSearchParams, options?: { replace?: boolean }) => void, searchParams: URLSearchParams, values: Record<string, string | null>) {
  const next = new URLSearchParams(searchParams);
  for (const [key, value] of Object.entries(values)) {
    if (value) next.set(key, value);
    else next.delete(key);
  }
  setSearchParams(next, { replace: true });
}

function mergeFilterNames(previous: Map<string, string>, entries: readonly (readonly [string, string])[]) {
  let next: Map<string, string> | undefined;
  for (const [id, name] of entries) {
    if (previous.get(id) !== name) {
      next ??= new Map(previous);
      next.set(id, name);
    }
  }
  return next ?? previous;
}

function filterOptions(names: Map<string, string>, selectedId: string, fallback: string) {
  const options = [...names.entries()];
  if (selectedId && !names.has(selectedId)) options.push([selectedId, fallback]);
  return options.sort(([, first], [, second]) => first.localeCompare(second));
}

function stat(label: string, value: string, detail?: string) {
  return <section className={dashboardStyles.card} aria-label={label} key={label}>
    <h2 className={dashboardStyles.label}>{label}</h2>
    <p className={dashboardStyles.value}>{value}</p>
    {detail && <p className={dashboardStyles.detail}>{detail}</p>}
  </section>;
}

function ScoreBadge({ overall }: { overall: number | null | undefined }) {
  return <Badge tone={scoreTone(overall)}>{overall == null ? "Not scored" : `${overall}/5`}</Badge>;
}

function dateLabel(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function durationLabel(startedAt: string, endedAt: string | null) {
  if (!endedAt) return "—";
  const seconds = Math.max(0, Math.round((Date.parse(endedAt) - Date.parse(startedAt)) / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function sentenceCase(value: string) {
  return value.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
}

function reviewText(item: AdminConversationItem) {
  return item.issues.length ? item.issues.join("; ") : "No issue notes";
}

function invalidateAdminQueries(queryClient: ReturnType<typeof useQueryClient>) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: ["admin-overview"] }),
    queryClient.invalidateQueries({ queryKey: ["admin-conversations"] }),
    queryClient.invalidateQueries({ queryKey: ["admin-conversation"] }),
  ]);
}

function isInteractiveTarget(target: EventTarget | null) {
  return target instanceof Element && Boolean(target.closest("a, button, input, select, textarea, [role='menu']"));
}

function RowActions({ item, reviewOnly, reviewPending, notice, onReview, onRescore, onMakeEvalCase }: {
  item: AdminConversationItem;
  reviewOnly: boolean;
  reviewPending: boolean;
  notice?: string;
  onReview: (id: string, reviewed: boolean) => void;
  onRescore: (id: string) => void;
  onMakeEvalCase: (id: string) => void;
}) {
  return <div className={styles.rowActionGroup}>
    <div className={styles.rowActions}>
      <button
        type="button"
        className="button-secondary button-sm"
        disabled={item.overall === null || reviewPending}
        onClick={() => onReview(item.conversation_id, !item.reviewed_at)}
      >{item.reviewed_at ? "Reopen" : "Mark reviewed"}</button>
      <MoreMenu
        label={item.assistant_name}
        items={[
          { label: "Rescore", onSelect: () => onRescore(item.conversation_id) },
          { label: "Make eval case", onSelect: () => onMakeEvalCase(item.conversation_id) },
        ]}
      />
    </div>
    {notice && <span className="muted" role="status">
      {notice}{notice === "Eval case created." && <> <Link to="/admin/evals">View evals</Link></>}
    </span>}
    {!reviewOnly && item.reviewed_at && <Badge>Reviewed</Badge>}
  </div>;
}

function ReviewItem({ item, reviewOnly, selected, reviewPending, notice, onSelect, onReview, onRescore, onMakeEvalCase }: {
  item: AdminConversationItem;
  reviewOnly: boolean;
  selected: boolean;
  reviewPending: boolean;
  notice?: string;
  onSelect: (id: string) => void;
  onReview: (id: string, reviewed: boolean) => void;
  onRescore: (id: string) => void;
  onMakeEvalCase: (id: string) => void;
}) {
  const navigate = useNavigate();
  return <article
    className={styles.reviewCard}
    onClick={(event) => {
      if (!isInteractiveTarget(event.target)) navigate(`/admin/conversations/${item.conversation_id}`);
    }}
  >
    <div className={styles.reviewCardHeading}>
      <input
        type="checkbox"
        aria-label={`Select ${item.assistant_name} conversation`}
        checked={selected}
        onChange={() => onSelect(item.conversation_id)}
      />
      <Link to={`/admin/conversations/${item.conversation_id}`}>{item.assistant_name}</Link>
    </div>
    <div className={styles.reviewMeta}>
      <time dateTime={item.started_at}>{dateLabel(item.started_at)}</time>
      <span aria-hidden="true">·</span>
      <span>{item.tenant_name}</span>
      <span aria-hidden="true">·</span>
      <span>{channelLabel(item.channel)}</span>
    </div>
    <div className={styles.reviewBadges}>
      <ScoreBadge overall={item.overall} />
      {item.is_test && <Badge>Test</Badge>}
    </div>
    <p className={styles.issueText}>{reviewText(item)}</p>
    <RowActions
      item={item}
      reviewOnly={reviewOnly}
      reviewPending={reviewPending}
      notice={notice}
      onReview={onReview}
      onRescore={onRescore}
      onMakeEvalCase={onMakeEvalCase}
    />
  </article>;
}

function AdminConversationTable({ items, reviewOnly, selectedIds, reviewPendingIds, actionNotices, onSelect, onSelectAll, onReview, onRescore, onMakeEvalCase }: {
  items: AdminConversationItem[];
  reviewOnly: boolean;
  selectedIds: Set<string>;
  reviewPendingIds: Set<string>;
  actionNotices: Record<string, string>;
  onSelect: (id: string) => void;
  onSelectAll: () => void;
  onReview: (id: string, reviewed: boolean) => void;
  onRescore: (id: string) => void;
  onMakeEvalCase: (id: string) => void;
}) {
  const navigate = useNavigate();
  const allSelected = items.length > 0 && items.every(({ conversation_id }) => selectedIds.has(conversation_id));
  const selectAll = <label className={styles.selectAll}>
    <input type="checkbox" aria-label="Select all conversations" checked={allSelected} onChange={onSelectAll} />
    <span>Select all</span>
  </label>;
  return <>
    <div className={styles.mobileSelectAll}>{selectAll}</div>
    <div className={`table-wrap ${styles.desktopList}`}>
      <table>
        <thead><tr><th>{selectAll}</th><th>Started</th><th>Workspace</th><th>Assistant</th><th>Channel</th><th>Score</th><th>Issues</th><th>Actions</th></tr></thead>
        <tbody>{items.map((item) => <tr
          key={item.conversation_id}
          className={styles.reviewRow}
          onClick={(event) => {
            if (!isInteractiveTarget(event.target)) navigate(`/admin/conversations/${item.conversation_id}`);
          }}
        >
          <td><input
            type="checkbox"
            aria-label={`Select ${item.assistant_name} conversation`}
            checked={selectedIds.has(item.conversation_id)}
            onChange={() => onSelect(item.conversation_id)}
          /></td>
          <td className="cell-muted">{dateLabel(item.started_at)}</td>
          <td>{item.tenant_name}</td>
          <td className="cell-primary"><Link to={`/admin/conversations/${item.conversation_id}`}>{item.assistant_name}</Link>{item.is_test && <> <Badge>Test</Badge></>}</td>
          <td>{channelLabel(item.channel)}</td>
          <td><ScoreBadge overall={item.overall} /></td>
          <td>{reviewText(item)}</td>
          <td><RowActions
            item={item}
            reviewOnly={reviewOnly}
            reviewPending={reviewPendingIds.has(item.conversation_id)}
            notice={actionNotices[item.conversation_id]}
            onReview={onReview}
            onRescore={onRescore}
            onMakeEvalCase={onMakeEvalCase}
          /></td>
        </tr>)}</tbody>
      </table>
    </div>
    <div className={styles.mobileList}>{items.map((item) => <ReviewItem
      key={item.conversation_id}
      item={item}
      reviewOnly={reviewOnly}
      selected={selectedIds.has(item.conversation_id)}
      reviewPending={reviewPendingIds.has(item.conversation_id)}
      notice={actionNotices[item.conversation_id]}
      onSelect={onSelect}
      onReview={onReview}
      onRescore={onRescore}
      onMakeEvalCase={onMakeEvalCase}
    />)}</div>
  </>;
}

export function AdminOverviewPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const days = [7, 30, 90].includes(Number(searchParams.get("days"))) ? Number(searchParams.get("days")) : 7;
  const tenantId = searchParams.get("tenant") ?? "";
  const assistantId = searchParams.get("assistant") ?? "";
  const reviewOnly = searchParams.get("review") !== "all";
  const queryClient = useQueryClient();
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [actionNotices, setActionNotices] = useState<Record<string, string>>({});
  const [bulkNotice, setBulkNotice] = useState("");
  const overview = useQuery({ queryKey: ["admin-overview", days], queryFn: () => getAdminOverview(days) });
  const conversations = useInfiniteQuery({
    queryKey: ["admin-conversations", tenantId, assistantId, reviewOnly],
    queryFn: ({ pageParam }) => listAdminConversations({
      tenant_id: tenantId || undefined,
      assistant_id: assistantId || undefined,
      needs_review: reviewOnly ? true : undefined,
      limit: 6,
      cursor: pageParam ?? undefined,
    }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
  });
  const items = useMemo(() => conversations.data?.pages.flatMap(({ items: pageItems }) => pageItems) ?? [], [conversations.data]);
  const data = overview.data;
  const [assistantNames, setAssistantNames] = useState(() => new Map<string, string>());
  const [workspaceNames, setWorkspaceNames] = useState(() => new Map<string, string>());
  const reviewMutation = useMutation({
    mutationFn: ({ conversationIds, reviewed }: { conversationIds: string[]; reviewed: boolean }) =>
      reviewConversations({ conversation_ids: conversationIds, reviewed }),
    onSuccess: async (_response, variables) => {
      setSelectedIds(new Set());
      if (variables.conversationIds.length > 1) {
        setBulkNotice(`${variables.conversationIds.length} conversations marked reviewed.`);
      } else {
        setActionNotices((previous) => ({
          ...previous,
          [variables.conversationIds[0]]: variables.reviewed ? "Marked reviewed" : "Review reopened",
        }));
      }
      await invalidateAdminQueries(queryClient);
    },
  });
  const rescore = useMutation({
    mutationFn: rescoreConversation,
    onSuccess: async (_response, id) => {
      setActionNotices((previous) => ({ ...previous, [id]: "Rescore queued" }));
      await invalidateAdminQueries(queryClient);
    },
    onError: (_error, id) => setActionNotices((previous) => ({ ...previous, [id]: "Rescore could not be queued." })),
  });
  const makeEvalCase = useMutation({
    mutationFn: createEvalCase,
    onSuccess: async (_response, id) => {
      setActionNotices((previous) => ({ ...previous, [id]: "Eval case created." }));
      await Promise.all([
        invalidateAdminQueries(queryClient),
        queryClient.invalidateQueries({ queryKey: ["admin-eval-cases"] }),
      ]);
    },
    onError: (_error, id) => setActionNotices((previous) => ({ ...previous, [id]: "The eval case could not be created." })),
  });

  useEffect(() => {
    setAssistantNames((previous) => mergeFilterNames(
      previous,
      items.map(({ assistant_id, assistant_name }) => [assistant_id, assistant_name] as const),
    ));
  }, [items]);

  useEffect(() => {
    setWorkspaceNames((previous) => mergeFilterNames(previous, [
      ...(data?.by_tenant ?? []).map(({ tenant_id, tenant_name }) => [tenant_id, tenant_name] as const),
      ...items.map(({ tenant_id, tenant_name }) => [tenant_id, tenant_name] as const),
    ]));
  }, [data?.by_tenant, items]);

  const assistants = filterOptions(assistantNames, assistantId, "Selected assistant");
  const workspaces = filterOptions(workspaceNames, tenantId, "Selected workspace");

  function update(values: Record<string, string | null>) {
    setSelectedIds(new Set());
    setBulkNotice("");
    updateParams(setSearchParams, searchParams, values);
  }

  function toggleSelected(id: string) {
    setSelectedIds((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAllSelected() {
    const allSelected = items.length > 0 && items.every(({ conversation_id }) => selectedIds.has(conversation_id));
    setSelectedIds(allSelected ? new Set() : new Set(items.map(({ conversation_id }) => conversation_id)));
  }

  function markReviewed(id: string, reviewed: boolean) {
    reviewMutation.mutate({ conversationIds: [id], reviewed });
  }

  const reviewPercent = data?.scored ? `${Math.round((data.needs_review / data.scored) * 100)}% of scored` : "—";
  const reviewPendingIds = new Set(reviewMutation.isPending ? reviewMutation.variables?.conversationIds ?? [] : []);

  return <main className="page" id="main-content">
    <PageHeader title="Admin" lede="Quality across all workspaces." />
    <AdminSubnav />
    <div className={`toolbar ${styles.rangeToolbar}`}>
      <div className="range-control">
        <span className="range-control-label">Time range</span>
        <div className="range-segments" role="group" aria-label="Time range">
          {[7, 30, 90].map((value) => <button type="button" key={value} aria-pressed={days === value} className={days === value ? "range-segment range-segment-active" : "range-segment"} onClick={() => update({ days: value === 7 ? null : String(value) })}>{value} days</button>)}
        </div>
      </div>
    </div>
    {overview.isLoading && <LoadingState label="Loading quality overview…" />}
    {overview.error && <ErrorState message="The quality overview could not be loaded." onRetry={() => void queryClient.invalidateQueries({ queryKey: ["admin-overview", days] })} />}
    {data && <>
      <div className={dashboardStyles.stats}>
        {stat("Conversations", String(data.conversations), `${data.scored} scored`)}
        {stat("Average score", data.avg_overall == null ? "—" : `${data.avg_overall.toFixed(1)} / 5`)}
        {stat("Needs review", String(data.needs_review), reviewPercent)}
        {stat("Response time", data.response_p50_s == null ? "—" : `p50 ${data.response_p50_s.toFixed(1)} s`, `p95 ${data.response_p95_s == null ? "—" : `${data.response_p95_s.toFixed(1)} s`}`)}
      </div>
      <section className="panel">
        <h2>Quality by day</h2>
        {data.by_day.length ? <AdminQualityChart series={data.by_day} /> : <EmptyState>No quality data for this period.</EmptyState>}
      </section>
      <section className="panel">
        <h2>By workspace</h2>
        {data.by_tenant.length ? <>
          <div className={`table-wrap ${styles.workspaceDesktop}`}><table className="admin-workspace-table">
          <thead><tr><th>Workspace</th><th>Conversations</th><th>Avg score</th><th>Needs review</th></tr></thead>
          <tbody>{data.by_tenant.map((tenant) => <tr key={tenant.tenant_id}>
            <td className="cell-primary">{tenant.tenant_name}</td><td>{tenant.conversations}</td><td>{tenant.avg_overall == null ? "—" : tenant.avg_overall.toFixed(1)}</td><td>{tenant.needs_review}</td>
          </tr>)}</tbody>
          </table></div>
          <ul className={styles.workspaceCards} aria-label="Workspace quality">
            {data.by_tenant.map((tenant) => <li key={tenant.tenant_id}>
              <strong>{tenant.tenant_name}</strong>
              <span>{tenant.conversations} conversations</span>
              <span>Average score {tenant.avg_overall == null ? "—" : tenant.avg_overall.toFixed(1)}</span>
              <span>Needs review {tenant.needs_review}</span>
            </li>)}
          </ul>
        </> : <EmptyState>No workspaces in this period.</EmptyState>}
      </section>
    </>}
    <section className="panel">
      <h2>Needs review</h2>
      <div className="toolbar">
        <div className="field"><label htmlFor="admin-workspace">Workspace</label>
          <select id="admin-workspace" value={tenantId} onChange={(event) => update({ tenant: event.target.value || null })}>
            <option value="">All workspaces</option>
            {workspaces.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
        </div>
        <div className="field"><label htmlFor="admin-assistant">Assistant</label>
          <select id="admin-assistant" value={assistantId} onChange={(event) => update({ assistant: event.target.value || null })}>
            <option value="">All assistants</option>
            {assistants.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
        </div>
        <label className="check-row"><input type="checkbox" checked={reviewOnly} onChange={(event) => update({ review: event.target.checked ? null : "all" })} />Needs review only</label>
      </div>
      {conversations.isLoading && <LoadingState label="Loading conversations…" />}
      {conversations.error && <ErrorState message="Conversations could not be loaded." onRetry={() => void conversations.refetch()} />}
      {!conversations.isLoading && !conversations.error && items.length === 0 && <EmptyState>No conversations match these filters.</EmptyState>}
      {items.length > 0 && <>
        {selectedIds.size > 0 && <div className={styles.bulkReviewBar}>
          <span>{selectedIds.size} selected</span>
          <span aria-hidden="true">·</span>
          <button
            type="button"
            className="button-primary button-sm"
            onClick={() => reviewMutation.mutate({ conversationIds: [...selectedIds], reviewed: true })}
            disabled={reviewMutation.isPending}
          >Mark reviewed</button>
        </div>}
        {bulkNotice && <p className="muted" role="status">{bulkNotice}</p>}
        {reviewMutation.error && <ErrorState message="The review status could not be updated." />}
        <AdminConversationTable
          items={items}
          reviewOnly={reviewOnly}
          selectedIds={selectedIds}
          reviewPendingIds={reviewPendingIds}
          actionNotices={actionNotices}
          onSelect={toggleSelected}
          onSelectAll={toggleAllSelected}
          onReview={markReviewed}
          onRescore={(id) => rescore.mutate(id)}
          onMakeEvalCase={(id) => makeEvalCase.mutate(id)}
        />
        {conversations.hasNextPage && <div className="button-row"><button type="button" className="button-secondary" onClick={() => void conversations.fetchNextPage()} disabled={conversations.isFetchingNextPage}>{conversations.isFetchingNextPage ? "Loading…" : "Load more"}</button></div>}
      </>}
    </section>
  </main>;
}

const checkLabels: Record<string, string> = {
  answered_question: "Answered the question",
  collected_required_details: "Collected required details",
  language_consistent: "Same language throughout",
  no_unsupported_promises: "No unsupported promises",
  ended_properly: "Ended properly",
  followed_instructions: "Followed instructions",
};

function ObjectiveMetrics({ objective }: { objective: AdminObjective | null }) {
  if (!objective) return <p className="muted">No call timing for this channel.</p>;
  const metrics: [string, number | null, string][] = [
    ["Median response", objective.response_p50_s, " s"],
    ["p95 response", objective.response_p95_s, " s"],
    ["Slowest response", objective.max_response_s, " s"],
    ["Interruptions", objective.interruptions, ""],
    ["Turns", objective.turns, ""],
    ["Call length", objective.call_duration_s, " s"],
    ["Spoken characters", objective.tts_chars, ""],
  ];
  return <dl className={styles.metricGrid}>{metrics.map(([label, value, suffix]) => <div key={label}><dt>{label}</dt><dd>{value == null ? "—" : `${value}${suffix}`}</dd></div>)}</dl>;
}

export function AdminConversationPage() {
  const { id = "" } = useParams();
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState("");
  const detail = useQuery({ queryKey: ["admin-conversation", id], queryFn: () => getAdminConversation(id), enabled: Boolean(id) });
  const rescore = useMutation({
    mutationFn: () => rescoreConversation(id),
    onSuccess: async () => {
      setNotice("Rescore queued");
      await invalidateAdminQueries(queryClient);
    },
  });
  const makeEvalCase = useMutation({
    mutationFn: () => createEvalCase(id),
    onSuccess: async () => {
      setNotice("Eval case created.");
      await Promise.all([
        invalidateAdminQueries(queryClient),
        queryClient.invalidateQueries({ queryKey: ["admin-eval-cases"] }),
      ]);
    },
  });
  const review = useMutation({
    mutationFn: (reviewed: boolean) => reviewConversations({ conversation_ids: [id], reviewed }),
    onSuccess: async (_response, reviewed) => {
      setNotice(reviewed ? "Marked reviewed" : "Review reopened");
      await invalidateAdminQueries(queryClient);
    },
  });
  const conversation = detail.data;
  const transcript = useMemo(() => conversation ? transcriptRows(conversation.messages, conversation.channel, conversation.started_at) : [], [conversation]);
  const messageLayout = conversation && !["phone", "web"].includes(conversation.channel);

  return <main className="page" id="main-content">
    <p className={styles.backLink}><Link to="/admin">Back to admin</Link></p>
    {detail.isLoading && <LoadingState label="Loading conversation…" />}
    {detail.error && <ErrorState message="The conversation could not be loaded." />}
    {conversation && <>
      <PageHeader title={`${conversation.assistant_name} · ${conversation.tenant_name}`} />
      <div className={styles.detailMeta}>
        <span>{channelLabel(conversation.channel)}</span><span aria-hidden="true">·</span>
        <span>{dateLabel(conversation.started_at)}</span><span aria-hidden="true">·</span>
        <span>Duration {durationLabel(conversation.started_at, conversation.ended_at)}</span>
        {conversation.is_test && <><span aria-hidden="true">·</span><Badge>Test</Badge></>}
        <span aria-hidden="true">·</span><Badge>{sentenceCase(conversation.status)}</Badge>
      </div>
      <div className="button-row">
        <button type="button" className="button-secondary" onClick={() => { setNotice(""); rescore.mutate(); }} disabled={rescore.isPending}>{rescore.isPending ? "Queueing…" : "Rescore"}</button>
        {conversation.score && <button
          type="button"
          className="button-secondary"
          onClick={() => { setNotice(""); review.mutate(!conversation.score?.reviewed_at); }}
          disabled={review.isPending}
        >{conversation.score.reviewed_at ? "Reopen" : "Mark reviewed"}</button>}
        <button type="button" className="button-secondary" onClick={() => { setNotice(""); makeEvalCase.mutate(); }} disabled={makeEvalCase.isPending}>{makeEvalCase.isPending ? "Creating…" : "Make eval case"}</button>
        {conversation.trace_url && <a className="button-ghost" href={conversation.trace_url} target="_blank" rel="noreferrer">Open trace</a>}
      </div>
      {notice && <p className="muted" role="status">{notice}{notice === "Eval case created." && <> <Link to="/admin/evals">View evals</Link></>}</p>}
      {rescore.error && <ErrorState message="Rescore could not be queued." />}
      {makeEvalCase.error && <ErrorState message="The eval case could not be created." />}
      {review.error && <ErrorState message="The review status could not be updated." />}
      <section className="panel">
        <h2>Quality score</h2>
        {conversation.score ? <>
          <div className="row"><p className={styles.scoreValue}>{conversation.score.overall}<span className="muted"> / 5</span></p>{conversation.score.needs_review && <Badge tone="warning">Needs review</Badge>}{conversation.score.reviewed_at && <Badge>Reviewed {dateLabel(conversation.score.reviewed_at)}</Badge>}</div>
          <p className="muted">Judge {conversation.score.judge_model} · rubric {conversation.score.judge_version}</p>
          <h3 className={styles.qualitySectionHeading}>Checks</h3>
          <ul className={styles.checkList}>{conversation.score.checks.map((check) => <li key={check.key}>
            <span>{checkLabels[check.key] ?? check.key}</span>
            <Badge tone={check.passed === false ? "danger" : check.passed === true ? "success" : "neutral"}>{check.passed === false ? "Failed" : check.passed === true ? "Passed" : "Not applicable"}</Badge>
            {check.passed === false && check.note && <p className={`muted ${styles.checkNote}`}>{check.note}</p>}
          </li>)}</ul>
          {conversation.score.issues.length > 0 && <><h3 className={styles.qualitySectionHeading}>Issues</h3><ul>{conversation.score.issues.map((issue) => <li key={issue}>{issue}</li>)}</ul></>}
          <h3 className={styles.qualitySectionHeading}>Objective metrics</h3>
          <ObjectiveMetrics objective={readObjective(conversation.score.objective)} />
        </> : <EmptyState title="Not scored yet">There is no quality score for this conversation.</EmptyState>}
      </section>
      <section className="panel">
        <h2>Transcript</h2>
        {transcript.length ? <ol className="call-transcript" data-layout={messageLayout ? "message" : undefined} aria-label="Conversation transcript">
          {transcript.map((row) => <li className="call-transcript-row" data-role={row.role} key={row.id}>
            {messageLayout ? <>
              <div className="call-transcript-meta"><strong className="call-transcript-speaker">{row.speaker}</strong><span className="call-transcript-date">· <time className="call-transcript-time" dateTime={row.ts}>{row.time}</time></span></div>
              <div className="call-transcript-text">{row.text}</div>
            </> : <>
              <time className="call-transcript-time" dateTime={row.ts}>{row.time}</time>
              <strong className="call-transcript-speaker">{row.speaker}</strong>
              <div className="call-transcript-text">{row.text}</div>
            </>}
          </li>)}
        </ol> : <EmptyState>No transcript messages are available.</EmptyState>}
      </section>
    </>}
  </main>;
}

function failureLabel(caseId: string, cases: { id: string; name: string }[]) {
  return cases.find(({ id }) => id === caseId)?.name ?? "Deleted case";
}

function EvalRunDetails({ run, cases }: { run: EvalRunResponse | undefined; cases: EvalCaseResponse[] }) {
  if (!run) return null;
  const results = readRunResults(run.results);
  const failedCount = results.filter(({ passed }) => !passed).length;
  const passedCount = results.filter(({ passed }) => passed).length;
  return <>
    <p className="muted">{passedCount} passed · {failedCount} failed</p>
    {results.filter(({ passed }) => !passed).map((result) => <section key={result.eval_case_id}>
      <h3>{failureLabel(result.eval_case_id, cases)}</h3>
      <ul>{result.failures.map((failure) => <li key={failure}>{failure}</li>)}</ul>
    </section>)}
    {failedCount === 0 && <p className="muted">All cases passed.</p>}
  </>;
}

export function AdminEvalsPage() {
  const [expandedId, setExpandedId] = useState("");
  const cases = useQuery({ queryKey: ["admin-eval-cases"], queryFn: listEvalCases });
  const runs = useQuery({ queryKey: ["admin-eval-runs"], queryFn: listEvalRuns });
  const expandedRun = useQuery({ queryKey: ["admin-eval-run", expandedId], queryFn: () => getEvalRun(expandedId), enabled: Boolean(expandedId) });
  const evalCases = cases.data ?? [];
  const evalRuns: EvalRunSummary[] = runs.data ?? [];

  return <main className="page" id="main-content">
    <PageHeader title="Evals" />
    <AdminSubnav />
    <section className="panel">
      <h2>Runs</h2>
      {runs.isLoading && <LoadingState label="Loading eval runs…" />}
      {runs.error && <ErrorState message="Eval runs could not be loaded." />}
      {!runs.isLoading && !runs.error && !evalRuns.length && <EmptyState>No eval runs yet.</EmptyState>}
      {evalRuns.length > 0 && <>
        <div className={`table-wrap ${styles.desktopRuns}`}><table>
          <thead><tr><th>Started</th><th>Trigger</th><th>Commit</th><th>Result</th></tr></thead>
          <tbody>{evalRuns.map((run) => {
            const expanded = expandedId === run.id;
            const summary = readRunSummary(run.summary);
            const tone = summary.failed ? "danger" : "success";
            return <Fragment key={run.id}>
              <tr>
                <td><button type="button" className={`button-link ${styles.runButton}`} aria-expanded={expanded} onClick={() => setExpandedId(expanded ? "" : run.id)}>{dateLabel(run.started_at)}</button></td>
                <td>{run.trigger}</td><td>{run.git_sha?.slice(0, 7) ?? "—"}</td>
                <td><Badge tone={tone}>{summary.passed}/{summary.cases} passed</Badge></td>
              </tr>
              {expanded && <tr><td colSpan={4}>
                {expandedRun.isLoading && <LoadingState label="Loading run results…" />}
                {expandedRun.error && <ErrorState message="The eval run could not be loaded." />}
                <div className={styles.runCases}><EvalRunDetails run={expandedRun.data} cases={evalCases} /></div>
              </td></tr>}
            </Fragment>;
          })}</tbody>
        </table></div>
        <ul className={styles.mobileRuns} aria-label="Eval runs">
          {evalRuns.map((run) => {
            const expanded = expandedId === run.id;
            const summary = readRunSummary(run.summary);
            const tone = summary.failed ? "danger" : "success";
            return <li className={styles.runCard} key={run.id}>
              <button type="button" className={`button-link ${styles.runButton}`} aria-expanded={expanded} onClick={() => setExpandedId(expanded ? "" : run.id)}>{dateLabel(run.started_at)}</button>
              <p className={styles.runMeta}>{run.trigger} · {run.git_sha?.slice(0, 7) ?? "—"}</p>
              <div><Badge tone={tone}>{summary.passed}/{summary.cases} passed</Badge></div>
              {expanded && <div className={styles.runCases}>
                {expandedRun.isLoading && <LoadingState label="Loading run results…" />}
                {expandedRun.error && <ErrorState message="The eval run could not be loaded." />}
                <EvalRunDetails run={expandedRun.data} cases={evalCases} />
              </div>}
            </li>;
          })}
        </ul>
      </>}
    </section>
    <section className="panel">
      <h2>Cases</h2>
      {cases.isLoading && <LoadingState label="Loading eval cases…" />}
      {cases.error && <ErrorState message="Eval cases could not be loaded." />}
      {!cases.isLoading && !cases.error && !evalCases.length && <EmptyState>No eval cases yet.</EmptyState>}
      {evalCases.map((evalCase) => {
        const checksCount = countCaseChecks(evalCase.checks);
        const callerTurns = evalCase.caller_utterances.length;
        return <article className={styles.evalCase} key={evalCase.id}>
          <div className="row"><strong>{evalCase.name}</strong><Badge tone={evalCase.enabled ? "success" : "neutral"}>{evalCase.enabled ? "Enabled" : "Disabled"}</Badge></div>
          <p className="muted">{callerTurns} caller {callerTurns === 1 ? "turn" : "turns"} · {checksCount} {checksCount === 1 ? "check" : "checks"}</p>
          {evalCase.source_conversation_id && <Link to={`/admin/conversations/${evalCase.source_conversation_id}`}>View source conversation</Link>}
        </article>;
      })}
    </section>
  </main>;
}
