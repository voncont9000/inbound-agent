import { Fragment, useEffect, useId, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api, unwrap, unwrapNoContent, type Schemas } from "../api/client";
import { ChannelIcon } from "../components/ChannelIcon";
import { ChannelPicker } from "../components/ChannelPicker";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { EmailIncludeFilters } from "../components/EmailIncludeFilters";
import { MoreMenu } from "../components/MoreMenu";
import { useGmailMode, useGoogleOAuth } from "../components/channelApi";
import { Badge, EmptyState, ErrorState, LoadingState, PageHeader } from "../components/States";
import { channelLabel, channelStatusTone } from "../lib/channelConnections";

type ChannelKind = "phone" | "whatsapp" | "gmail";
type KindFilter = "all" | "phone" | "gmail" | "whatsapp";
type Channel = Schemas["ChannelConnectionResponse"];

const kindFilters: { value: KindFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "phone", label: "Phone" },
  { value: "gmail", label: "Email" },
  { value: "whatsapp", label: "WhatsApp" },
];

function ChannelMenu({ channel, filtersOpen, onToggleFilters, onRemove }: {
  channel: Channel;
  filtersOpen: boolean;
  onToggleFilters: () => void;
  onRemove: () => void;
}) {
  return <MoreMenu
    label={channel.external_id}
    items={[
      ...(channel.kind === "gmail" ? [{
        label: filtersOpen ? "Hide email filters" : "Email filters",
        onSelect: onToggleFilters,
      }] : []),
      { label: "Remove", onSelect: onRemove },
    ]}
  />;
}

export function ChannelsPage() {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const addButtonRef = useRef<HTMLButtonElement>(null);
  const assistantSelectRef = useRef<HTMLSelectElement>(null);
  const inputFocusRef = useRef<HTMLInputElement>(null);
  const pickerId = useId();
  const addFormId = useId();
  const [search, setSearch] = useState("");
  const [kindFilter, setKindFilter] = useState<KindFilter>("all");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [selectedKind, setSelectedKind] = useState<ChannelKind | null>(null);
  const [assistantSelection, setAssistantSelection] = useState("");
  const [externalId, setExternalId] = useState("");
  const [formError, setFormError] = useState("");
  const [openFiltersId, setOpenFiltersId] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<Channel | null>(null);
  const [pendingReassignment, setPendingReassignment] = useState<{ channel: Channel; assistantId: string } | null>(null);
  const [actionError, setActionError] = useState("");
  const [restoreAddFocus, setRestoreAddFocus] = useState(false);
  const initializedSelection = useRef(false);
  const assistantFilterId = searchParams.get("assistant") ?? "";
  const connectedGmail = searchParams.get("connected") === "gmail";
  const gmailInUse = searchParams.get("error") === "gmail_in_use";

  const assistants = useQuery({ queryKey: ["assistants"], queryFn: () => unwrap(api.GET("/assistants")) });
  const channels = useQuery({ queryKey: ["workspace-channels"], queryFn: () => unwrap(api.GET("/channels")) });
  const gmailMode = useGmailMode();
  const assistantsList = useMemo(() => assistants.data?.items ?? [], [assistants.data?.items]);
  const channelList = channels.data?.items ?? [];
  const assistantById = new Map(assistantsList.map((assistant) => [assistant.id, assistant]));
  const gmailAssistantIds = new Set(channelList.flatMap(({ kind, assistant_id }) =>
    kind === "gmail" && assistant_id ? [assistant_id] : [],
  ));
  const gmailAvailable = assistantsList.some(({ id }) => !gmailAssistantIds.has(id));
  const filteredAssistant = assistantFilterId ? assistantById.get(assistantFilterId) : undefined;

  useEffect(() => {
    if (initializedSelection.current || !assistants.data) return;
    const fromQuery = assistantFilterId && assistantsList.some(({ id }) => id === assistantFilterId)
      ? assistantFilterId
      : "";
    setAssistantSelection(fromQuery || (assistantsList.length === 1 ? assistantsList[0].id : ""));
    initializedSelection.current = true;
  }, [assistantFilterId, assistants.data, assistantsList]);

  useEffect(() => {
    if (!selectedKind) return;
    if (selectedKind === "gmail" && gmailMode.data?.mode === "google") assistantSelectRef.current?.focus();
    else inputFocusRef.current?.focus();
  }, [gmailMode.data?.mode, selectedKind]);

  useEffect(() => {
    if (pickerOpen || selectedKind || pendingDelete || pendingReassignment || !restoreAddFocus) return;
    addButtonRef.current?.focus();
    setRestoreAddFocus(false);
  }, [pickerOpen, pendingDelete, pendingReassignment, restoreAddFocus, selectedKind]);

  useEffect(() => {
    if (!pickerOpen && !selectedKind) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setPickerOpen(false);
      setSelectedKind(null);
      setExternalId("");
      setFormError("");
      setRestoreAddFocus(true);
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [pickerOpen, selectedKind]);

  const refreshChannels = async () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ["workspace-channels"] }),
    queryClient.invalidateQueries({ queryKey: ["assistant-channels"] }),
  ]);

  const add = useMutation({
    mutationFn: () => {
      if (!selectedKind || !assistantSelection) throw new Error("Choose an assistant.");
      return unwrap(api.POST("/channels", {
        body: {
          kind: selectedKind,
          external_id: externalId.trim(),
          assistant_id: assistantSelection,
          settings: {},
        },
      }));
    },
    onSuccess: async () => {
      setExternalId("");
      setFormError("");
      setPickerOpen(false);
      setSelectedKind(null);
      setRestoreAddFocus(true);
      await refreshChannels();
    },
    onError: (caught) => setFormError(caught instanceof Error ? caught.message : "Unable to add channel."),
  });

  const startGmailOAuth = useGoogleOAuth(assistantSelection, undefined, setFormError);
  const remove = useMutation({
    mutationFn: (channel: Channel) => unwrapNoContent(api.DELETE(
      "/channels/{channel_id}", { params: { path: { channel_id: channel.id } } },
    )),
    onSuccess: async () => {
      setPendingDelete(null);
      setActionError("");
      await refreshChannels();
    },
    onError: (caught) => setActionError(caught instanceof Error ? caught.message : "Unable to remove channel."),
  });
  const reassign = useMutation({
    mutationFn: ({ channel, assistantId }: { channel: Channel; assistantId: string }) => unwrap(api.PATCH(
      "/channels/{channel_id}",
      { params: { path: { channel_id: channel.id } }, body: { assistant_id: assistantId } },
    )),
    onSuccess: async () => {
      setPendingReassignment(null);
      setActionError("");
      await refreshChannels();
    },
    onError: (caught) => setActionError(caught instanceof Error ? caught.message : "Unable to reassign channel."),
  });

  const term = search.trim().toLocaleLowerCase();
  const visibleChannels = channelList.filter((channel) => {
    const assistantName = assistantById.get(channel.assistant_id ?? "")?.name ?? "";
    return (kindFilter === "all" || channel.kind === kindFilter)
      && (!assistantFilterId || channel.assistant_id === assistantFilterId)
      && (!term || channel.external_id.toLocaleLowerCase().includes(term) || assistantName.toLocaleLowerCase().includes(term));
  });

  const startAdd = () => {
    setFormError("");
    setRestoreAddFocus(false);
    if (selectedKind) {
      setSelectedKind(null);
      setExternalId("");
      setPickerOpen(true);
      return;
    }
    setPickerOpen((current) => !current);
  };
  const cancelAdd = () => {
    setPickerOpen(false);
    setSelectedKind(null);
    setExternalId("");
    setFormError("");
    setRestoreAddFocus(true);
  };
  const removeAssistantFilter = () => {
    const next = new URLSearchParams(searchParams);
    next.delete("assistant");
    setSearchParams(next, { replace: true });
  };

  return <main className="page" id="main-content">
    <PageHeader
      title="Channels"
      actions={<button
        ref={addButtonRef}
        type="button"
        className="button-primary"
        aria-expanded={pickerOpen || Boolean(selectedKind)}
        aria-controls={pickerId}
        onClick={startAdd}
      >Add channel</button>}
    />
    {connectedGmail && <p className="channel-connected-notice" role="status">Gmail connected.</p>}
    {gmailInUse && <p role="alert">This Gmail address is already connected in another workspace.</p>}
    {(pickerOpen || selectedKind) && <section className="channel-add-area" id={pickerId} aria-label="Add channel">
      {pickerOpen && !selectedKind && <ChannelPicker
        gmailMode={gmailMode.data?.mode}
        gmailAvailable={gmailAvailable}
        onSelect={(kind) => {
          setPickerOpen(false);
          setSelectedKind(kind);
          setFormError("");
        }}
        onCancel={cancelAdd}
      />}
      {selectedKind && <form
        className="channel-add-form workspace-channel-form"
        id={addFormId}
        aria-label={`Add ${selectedKind} channel`}
        onSubmit={(event) => {
          event.preventDefault();
          setFormError("");
          add.mutate();
        }}
      >
        <div className="field">
          <label htmlFor={`${addFormId}-assistant`}>Assistant</label>
          <select
            ref={assistantSelectRef}
            id={`${addFormId}-assistant`}
            value={assistantSelection}
            onChange={(event) => setAssistantSelection(event.target.value)}
            required
          >
            <option value="">Choose an assistant</option>
            {assistantsList.map((assistant) => {
              const hasGmail = gmailAssistantIds.has(assistant.id);
              return <option
                key={assistant.id}
                value={assistant.id}
                disabled={selectedKind === "gmail" && hasGmail}
              >{assistant.name}{selectedKind === "gmail" && hasGmail ? " (has Gmail)" : ""}</option>;
            })}
          </select>
        </div>
        {selectedKind === "phone" && <>
          <div className="field">
            <label htmlFor={`${addFormId}-external`}>Phone number</label>
            <input
              ref={inputFocusRef}
              id={`${addFormId}-external`}
              type="tel"
              placeholder="+44 20 7946 0000"
              value={externalId}
              required
              onChange={(event) => setExternalId(event.target.value)}
            />
          </div>
          <p className="help">Enter the number in international format, starting with + and the country code.</p>
        </>}
        {selectedKind === "whatsapp" && <>
          <div className="field">
            <label htmlFor={`${addFormId}-external`}>WhatsApp phone_number_id</label>
            <input
              ref={inputFocusRef}
              id={`${addFormId}-external`}
              value={externalId}
              required
              onChange={(event) => setExternalId(event.target.value)}
            />
          </div>
          <p className="help">Enter the phone_number_id from your Meta WhatsApp Business account.</p>
        </>}
        {selectedKind === "gmail" && gmailMode.data?.mode === "fake" && <>
          <div className="field">
            <label htmlFor={`${addFormId}-external`}>Mailbox email address</label>
            <input
              ref={inputFocusRef}
              id={`${addFormId}-external`}
              type="email"
              value={externalId}
              required
              onChange={(event) => setExternalId(event.target.value)}
            />
          </div>
          <p className="help">Drafts are created for review; mail is never sent automatically.</p>
        </>}
        {selectedKind === "gmail" && gmailMode.data?.mode === "google" && <p className="help">Connect the selected assistant’s Gmail inbox with Google.</p>}
        {formError && <p role="alert">{formError}</p>}
        <div className="button-row">
          {selectedKind === "gmail" && gmailMode.data?.mode === "google"
            ? <button
              className="button-primary"
              type="button"
              disabled={!assistantSelection || gmailAssistantIds.has(assistantSelection) || startGmailOAuth.isPending}
              onClick={() => startGmailOAuth.mutate()}
            >{startGmailOAuth.isPending ? "Connecting…" : "Connect with Google"}</button>
            : <button
              className="button-primary"
              type="submit"
              disabled={add.isPending || !assistantSelection || !externalId.trim()}
            >{add.isPending ? "Adding…" : "Add channel"}</button>}
          <button type="button" className="button-ghost" onClick={cancelAdd}>Cancel</button>
        </div>
      </form>}
    </section>}
    {actionError && <p role="alert">{actionError}</p>}
    <div className="channel-workspace-controls">
      <div className="field channel-search-field">
        <label htmlFor="channel-search">Search channels</label>
        <input id="channel-search" type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search number, address or assistant" />
      </div>
      <div className="range-control">
        <span className="range-control-label">Channel type</span>
        <div className="range-segments" role="group" aria-label="Channel type">
          {kindFilters.map(({ value, label }) => <button
            key={value}
            type="button"
            aria-pressed={kindFilter === value}
            className={kindFilter === value ? "range-segment range-segment-active" : "range-segment"}
            onClick={() => setKindFilter(value)}
          >{label}</button>)}
        </div>
      </div>
      {assistantFilterId && <button type="button" className="channel-assistant-filter" onClick={removeAssistantFilter}>
        Assistant: {filteredAssistant?.name ?? "Unknown assistant"} <span aria-hidden="true">×</span>
        <span className="sr-only">Remove assistant filter</span>
      </button>}
    </div>
    {channels.isLoading && <LoadingState label="Loading channels…" />}
    {channels.error && <ErrorState message="Channel connections could not be loaded." />}
    {channels.data && !channelList.length
      ? <EmptyState title="No channels yet" action={<button type="button" className="button-primary" onClick={startAdd}>Add channel</button>}>
        Connect a phone number, email inbox or WhatsApp channel to an assistant.
      </EmptyState>
      : channels.data && !visibleChannels.length && <EmptyState>No channels match these filters.</EmptyState>}
    {!!visibleChannels.length && <div className="channel-table-wrap">
      <table className="channel-table">
        <thead><tr><th scope="col">Channel</th><th scope="col">Number / address</th><th scope="col">Assistant</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
        <tbody>
          {visibleChannels.map((channel) => {
            const filtersOpen = openFiltersId === channel.id;
            return <Fragment key={channel.id}>
              <tr className="channel-table-row">
                <td data-label="Channel"><span className="channel-table-name"><ChannelIcon kind={channel.kind} size={18} />{channelLabel(channel.kind)}</span></td>
                <td data-label="Number / address"><span className="channel-table-id">{channel.external_id}</span></td>
                <td data-label="Assistant">{channel.assistant_id === null && <Badge tone="neutral">Unassigned</Badge>}<select
                  aria-label={`Assistant for ${channel.external_id}`}
                  value={channel.assistant_id ?? ""}
                  onChange={(event) => {
                    if (channel.kind === "gmail") setPendingReassignment({ channel, assistantId: event.target.value });
                    else reassign.mutate({ channel, assistantId: event.target.value });
                  }}
                >
                  {channel.assistant_id === null && <option value="" disabled>Unassigned — choose an assistant</option>}
                  {assistantsList.map((assistant) => {
                    const hasGmail = gmailAssistantIds.has(assistant.id);
                    return <option
                      key={assistant.id}
                      value={assistant.id}
                      disabled={channel.kind === "gmail" && assistant.id !== channel.assistant_id && hasGmail}
                    >{assistant.name}{channel.kind === "gmail" && assistant.id !== channel.assistant_id && hasGmail ? " (has Gmail)" : ""}</option>;
                  })}
                </select></td>
                <td data-label="Status"><Badge tone={channelStatusTone(channel.status)}>{channel.status.charAt(0).toUpperCase()}{channel.status.slice(1)}</Badge></td>
                <td data-label="Actions"><ChannelMenu
                  channel={channel}
                  filtersOpen={filtersOpen}
                  onToggleFilters={() => setOpenFiltersId(filtersOpen ? null : channel.id)}
                  onRemove={() => { setActionError(""); setPendingDelete(channel); }}
                /></td>
              </tr>
              {filtersOpen && <tr className="channel-details-row"><td colSpan={5}><EmailIncludeFilters channel={channel} /></td></tr>}
            </Fragment>;
          })}
        </tbody>
      </table>
    </div>}
    {pendingDelete && <ConfirmDialog
      title={`Remove ${channelLabel(pendingDelete.kind)}?`}
      confirmLabel="Remove"
      tone="danger"
      onCancel={() => { setPendingDelete(null); setActionError(""); }}
      onConfirm={() => remove.mutate(pendingDelete)}
    >
      <p>{pendingDelete.kind === "gmail"
        ? "This also disconnects Google Calendar and Sheets for this assistant because they share the same Google connection."
        : "This removes the channel from this assistant."}</p>
      {actionError && <p role="alert">{actionError}</p>}
    </ConfirmDialog>}
    {pendingReassignment && <ConfirmDialog
      title="Move Gmail connection?"
      confirmLabel="Move inbox"
      onCancel={() => setPendingReassignment(null)}
      onConfirm={() => reassign.mutate(pendingReassignment)}
    >
      <p>Calendar booking and Google Sheets actions move with this inbox to {assistantById.get(pendingReassignment.assistantId)?.name ?? "the selected assistant"}.</p>
      {actionError && <p role="alert">{actionError}</p>}
    </ConfirmDialog>}
  </main>;
}
