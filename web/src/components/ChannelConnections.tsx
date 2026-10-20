import { useEffect, useId, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap, unwrapNoContent, type Schemas } from "../api/client";
import { ChannelIcon } from "./ChannelIcon";
import { ConfirmDialog } from "./ConfirmDialog";
import { Badge, ErrorState, LoadingState } from "./States";

type ChannelKind = "phone" | "whatsapp" | "gmail";
type IncludeRule = { field: "from" | "to" | "subject"; contains: string };

function useGmailMode() {
  return useQuery({ queryKey: ["gmail-mode"], queryFn: () => unwrap(api.GET("/channels/gmail/mode")) });
}

function useChannels(assistantId: string) {
  return useQuery({
    queryKey: ["assistant-channels", assistantId],
    queryFn: () => unwrap(api.GET("/assistants/{assistant_id}/channels", { params: { path: { assistant_id: assistantId } } })),
  });
}

function useGoogleOAuth(assistantId: string, features: Schemas["GmailOAuthStartRequest"]["features"], onError: (message: string) => void) {
  return useMutation({
    mutationFn: () => unwrap(api.POST("/assistants/{assistant_id}/channels/gmail/oauth-start", {
      params: { path: { assistant_id: assistantId } },
      ...(features ? { body: { features } } : {}),
    })),
    onSuccess: ({ url }) => window.location.assign(url),
    onError: (caught) => onError(caught instanceof Error ? caught.message : "Unable to connect Google."),
  });
}

function channelLabel(kind: Schemas["ChannelConnectionResponse"]["kind"]) {
  if (kind === "gmail") return "Email (Gmail)";
  if (kind === "whatsapp") return "WhatsApp";
  if (kind === "web") return "Browser";
  return "Phone";
}

function channelStatusTone(status: string): "neutral" | "success" | "warning" | "danger" | "info" {
  if (status.toLowerCase() === "active") return "success";
  if (status.toLowerCase() === "failed") return "danger";
  return "neutral";
}

function readIncludeRules(settings: Record<string, unknown>): IncludeRule[] {
  const value = settings.include_rules;
  if (!Array.isArray(value)) return [];
  return value.flatMap((item): IncludeRule[] => {
    if (!item || typeof item !== "object") return [];
    const { field, contains } = item as Record<string, unknown>;
    if ((field !== "from" && field !== "to" && field !== "subject") || typeof contains !== "string") {
      return [];
    }
    return [{ field, contains }];
  });
}

function describeRules(rules: IncludeRule[]) {
  if (!rules.length) return "All emails";
  const summary = rules.map(({ field, contains }) => {
    const label = field === "to" ? "To" : field === "from" ? "From" : "Subject";
    return `${label} contains ${contains}`;
  }).join("; ");
  return `Only emails where: ${summary}`;
}

function EmailIncludeFilters({ assistantId, channel }: {
  assistantId: string;
  channel: Schemas["ChannelConnectionResponse"];
}) {
  const queryClient = useQueryClient();
  const panelId = useId();
  const savedRules = readIncludeRules(channel.settings);
  const [open, setOpen] = useState(false);
  const [rules, setRules] = useState<IncludeRule[]>(savedRules);
  const [saveMessage, setSaveMessage] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const saveFilters = useMutation({
    mutationFn: (includeRules: IncludeRule[]) => unwrap(api.PATCH(
      "/assistants/{assistant_id}/channels/{channel_id}",
      {
        params: { path: { assistant_id: assistantId, channel_id: channel.id } },
        body: { include_rules: includeRules },
      },
    )),
    onSuccess: async () => {
      setSaveMessage({ kind: "success", text: "Filters saved." });
      await queryClient.invalidateQueries({ queryKey: ["assistant-channels", assistantId] });
    },
    onError: (caught) => setSaveMessage({
      kind: "error",
      text: caught instanceof Error ? caught.message : "Unable to save email filters.",
    }),
  });

  const toggleOpen = () => {
    if (!open) {
      setRules(readIncludeRules(channel.settings));
      setSaveMessage(null);
    }
    setOpen((current) => !current);
  };

  return <div className="channel-filter-section">
    <button type="button" className="channel-filter-toggle" aria-expanded={open} aria-controls={panelId} onClick={toggleOpen}>
      <span>Email filters</span>
      <span className="muted">{describeRules(savedRules)}</span>
      <span aria-hidden="true">{open ? "−" : "+"}</span>
    </button>
    {open && <div className="channel-filter-editor" id={panelId}>
      <p className="help">If you add filters, only emails matching at least one of them reach the assistant. Others are left in Gmail, labelled AI/Skipped.</p>
      <div className="channel-filter-list">
        {rules.map((rule, index) => <div className="channel-filter-row" key={index}>
          <div className="field">
            <label className="sr-only" htmlFor={`${panelId}-field-${index}`}>Filter field {index + 1}</label>
            <select
              id={`${panelId}-field-${index}`}
              aria-label={`Filter field ${index + 1}`}
              value={rule.field}
              onChange={(event) => {
                const field = event.target.value as IncludeRule["field"];
                setRules((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, field } : item));
                setSaveMessage(null);
              }}
            >
              <option value="to">Sent to (To)</option>
              <option value="from">From</option>
              <option value="subject">Subject</option>
            </select>
          </div>
          <div className="field">
            <label className="sr-only" htmlFor={`${panelId}-contains-${index}`}>Contains filter {index + 1}</label>
            <input
              id={`${panelId}-contains-${index}`}
              aria-label={`Contains filter ${index + 1}`}
              value={rule.contains}
              maxLength={200}
              placeholder="contains"
              onChange={(event) => {
                const contains = event.target.value;
                setRules((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, contains } : item));
                setSaveMessage(null);
              }}
            />
          </div>
          <button
            type="button"
            className="button-ghost button-sm"
            aria-label={`Remove filter ${index + 1}`}
            onClick={() => {
              setRules((current) => current.filter((_, itemIndex) => itemIndex !== index));
              setSaveMessage(null);
            }}
          >
            Remove
          </button>
        </div>)}
      </div>
      <div className="button-row">
        <button
          type="button"
          className="button-ghost"
          disabled={rules.length >= 20}
          onClick={() => {
            setRules((current) => [...current, { field: "to", contains: "" }]);
            setSaveMessage(null);
          }}
        >
          Add filter
        </button>
        <button
          type="button"
          className="button-primary"
          disabled={saveFilters.isPending || rules.some(({ contains }) => !contains.trim())}
          onClick={() => saveFilters.mutate(rules.map((rule) => ({ ...rule, contains: rule.contains.trim() })))}
        >
          {saveFilters.isPending ? "Saving…" : "Save filters"}
        </button>
      </div>
      {saveMessage && <p className={saveMessage.kind === "error" ? "field-error" : "help"} role={saveMessage.kind === "error" ? "alert" : "status"}>{saveMessage.text}</p>}
    </div>}
  </div>;
}

export function ChannelConnections({ assistantId }: { assistantId: string }) {
  const queryClient = useQueryClient();
  const inputId = useId();
  const pickerId = useId();
  const addButtonRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const focusAddButtonOnClose = useRef(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [selectedKind, setSelectedKind] = useState<ChannelKind | null>(null);
  const [externalId, setExternalId] = useState("");
  const [error, setError] = useState("");
  const [pendingDelete, setPendingDelete] = useState<Schemas["ChannelConnectionResponse"] | null>(null);
  const [deleteError, setDeleteError] = useState("");
  const gmailMode = useGmailMode();
  const channels = useChannels(assistantId);
  const startGmailOAuth = useGoogleOAuth(assistantId, undefined, setError);
  const gmailConnected = channels.data?.items.some((channel) => channel.kind === "gmail") ?? false;

  const add = useMutation({
    mutationFn: () => {
      if (!selectedKind) throw new Error("Choose a channel type.");
      return unwrap(api.POST("/assistants/{assistant_id}/channels", {
        params: { path: { assistant_id: assistantId } },
        body: { kind: selectedKind, external_id: externalId.trim(), settings: {} },
      }));
    },
    onSuccess: async () => {
      setExternalId("");
      setError("");
      focusAddButtonOnClose.current = true;
      setSelectedKind(null);
      setPickerOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["assistant-channels", assistantId] });
    },
    onError: (caught) => setError(caught instanceof Error ? caught.message : "Unable to add channel."),
  });
  const remove = useMutation({
    mutationFn: (channel: Schemas["ChannelConnectionResponse"]) => unwrapNoContent(api.DELETE(
      "/assistants/{assistant_id}/channels/{channel_id}",
      { params: { path: { assistant_id: assistantId, channel_id: channel.id } } },
    )),
    onSuccess: async () => {
      focusAddButtonOnClose.current = true;
      setPendingDelete(null);
      setDeleteError("");
      await queryClient.invalidateQueries({ queryKey: ["assistant-channels", assistantId] });
    },
    onError: (caught) => setDeleteError(caught instanceof Error ? caught.message : "Unable to remove channel."),
  });

  useEffect(() => {
    if (!pickerOpen && !selectedKind) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      focusAddButtonOnClose.current = true;
      setPickerOpen(false);
      setSelectedKind(null);
      setExternalId("");
      setError("");
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [pickerOpen, selectedKind]);

  useEffect(() => {
    if (pickerOpen || selectedKind || pendingDelete || !focusAddButtonOnClose.current) return;
    focusAddButtonOnClose.current = false;
    addButtonRef.current?.focus();
  }, [pickerOpen, pendingDelete, selectedKind]);

  useEffect(() => {
    if (selectedKind) inputRef.current?.focus();
  }, [selectedKind]);

  const chooseKind = (kind: ChannelKind) => {
    setError("");
    if (kind === "gmail" && gmailMode.data?.mode === "google") {
      setPickerOpen(false);
      startGmailOAuth.mutate();
      return;
    }
    setPickerOpen(false);
    setSelectedKind(kind);
  };

  const cancelAdd = () => {
    focusAddButtonOnClose.current = true;
    setPickerOpen(false);
    setSelectedKind(null);
    setExternalId("");
    setError("");
  };

  return <section className="panel stack" aria-labelledby="assistant-channels-heading">
    <h2 id="assistant-channels-heading">Channels</h2>
    <p className="muted">Connect the ways people reach this assistant.</p>
    {channels.isLoading && <LoadingState />}
    {channels.error && <ErrorState message="Channel connections could not be loaded." />}
    {channels.data && !channels.data.items.length && <p className="muted">No channels connected yet.</p>}
    {!!channels.data?.items.length && <div className="channel-connection-list">
      {channels.data.items.map((channel) => <article className="channel-connection" key={channel.id}>
        <div className="channel-connection-row">
          <div className="channel-connection-details">
            <ChannelIcon kind={channel.kind} size={18} />
            <strong>{channelLabel(channel.kind)}</strong>
            <span className="channel-connection-id">{channel.external_id}</span>
          </div>
          <Badge tone={channelStatusTone(channel.status)}>{channel.status.charAt(0).toUpperCase()}{channel.status.slice(1)}</Badge>
          <button
            type="button"
            className="button-ghost button-sm"
            onClick={() => {
              setDeleteError("");
              setPendingDelete(channel);
            }}
          >
            Remove
          </button>
        </div>
        {channel.kind === "gmail" && <EmailIncludeFilters assistantId={assistantId} channel={channel} />}
      </article>)}
    </div>}
    {gmailMode.data?.mode === "off" && <p className="help">Gmail is not enabled for this deployment.</p>}
    {error && <p role="alert">{error}</p>}
    {selectedKind && <form
      className="channel-add-form"
      onSubmit={(event) => {
        event.preventDefault();
        setError("");
        add.mutate();
      }}
    >
      <div className="field">
        <label htmlFor={`channel-external-id-${inputId}`}>
          {selectedKind === "phone" ? "Phone number" : selectedKind === "whatsapp" ? "WhatsApp phone_number_id" : "Mailbox email address"}
        </label>
        <input
          ref={inputRef}
          id={`channel-external-id-${inputId}`}
          type={selectedKind === "phone" ? "tel" : selectedKind === "gmail" ? "email" : "text"}
          placeholder={selectedKind === "phone" ? "+44 20 7946 0000" : undefined}
          value={externalId}
          required
          onChange={(event) => setExternalId(event.target.value)}
        />
      </div>
      {selectedKind === "phone" && <p className="help">Enter the number in international format, starting with + and the country code.</p>}
      {selectedKind === "whatsapp" && <p className="help">Enter the phone_number_id from your Meta WhatsApp Business account.</p>}
      {selectedKind === "gmail" && <p className="help">Enter the mailbox email address. Drafts are created for review; mail is never sent automatically.</p>}
      <div className="button-row">
        <button className="button-primary" type="submit" disabled={add.isPending || !externalId.trim()}>
          {add.isPending ? "Adding…" : "Add channel"}
        </button>
        <button type="button" className="button-ghost" onClick={cancelAdd}>Cancel</button>
      </div>
    </form>}
    {!selectedKind && <button
      ref={addButtonRef}
      type="button"
      className="button-primary channel-add-trigger"
      aria-expanded={pickerOpen}
      aria-controls={pickerId}
      onClick={() => {
        setError("");
        focusAddButtonOnClose.current = true;
        setPickerOpen((current) => !current);
      }}
    >
      Add a channel
    </button>}
    {pickerOpen && <div className="post-action-picker channel-add-picker" id={pickerId}>
      <h3>Choose a channel</h3>
      <div className="post-action-picker-grid">
        <button type="button" className="post-action-picker-card" onClick={() => chooseKind("phone")}>
          <ChannelIcon kind="phone" size={20} />
          <span className="post-action-picker-copy"><strong>Phone number</strong><span>Connect a number routed to this assistant.</span></span>
        </button>
        <button type="button" className="post-action-picker-card" onClick={() => chooseKind("whatsapp")}>
          <ChannelIcon kind="whatsapp" size={20} />
          <span className="post-action-picker-copy"><strong>WhatsApp</strong><span>Connect using a WhatsApp phone number ID.</span></span>
        </button>
        {gmailMode.data && gmailMode.data.mode !== "off" && !gmailConnected && <button type="button" className="post-action-picker-card" onClick={() => chooseKind("gmail")}>
          <ChannelIcon kind="gmail" size={20} />
          <span className="post-action-picker-copy"><strong>Gmail inbox</strong><span>{gmailMode.data.mode === "google" ? "Connect with Google." : "Connect a mailbox for draft-only email triage."}</span></span>
        </button>}
      </div>
      <button type="button" className="button-ghost post-action-picker-cancel" onClick={cancelAdd}>Cancel</button>
    </div>}
    {pendingDelete && <ConfirmDialog
      title={`Remove ${channelLabel(pendingDelete.kind)}?`}
      confirmLabel="Remove"
      tone="danger"
      onCancel={() => {
        setPendingDelete(null);
        setDeleteError("");
      }}
      onConfirm={() => remove.mutate(pendingDelete)}
    >
      <p>{pendingDelete.kind === "gmail"
        ? "This also disconnects Google Calendar and Sheets for this assistant because they share the same Google connection."
        : "This removes the channel from this assistant."}</p>
      {deleteError && <p role="alert">{deleteError}</p>}
    </ConfirmDialog>}
  </section>;
}

export function CalendarConnection({ assistantId }: { assistantId: string }) {
  const [error, setError] = useState("");
  const gmailMode = useGmailMode();
  const calendarMode = useQuery({ queryKey: ["calendar-mode"], queryFn: () => unwrap(api.GET("/calendar/mode")) });
  const channels = useChannels(assistantId);
  const startCalendarOAuth = useGoogleOAuth(assistantId, ["calendar"], setError);
  const calendarConnected = channels.data?.items.some((channel) => channel.kind === "gmail" && channel.google_features.includes("calendar")) ?? false;
  return <div className="stack">
    {calendarMode.data?.mode === "off" && <p className="help">Calendar booking is not enabled for this deployment. Ask your administrator to enable Calendar integration.</p>}
    {calendarMode.data?.mode === "fake" && <p className="help">
      The fake Calendar provider is enabled for this deployment. {calendarConnected
        ? "Google Calendar is connected for this assistant."
        : "Google Calendar is not connected for this assistant."}
    </p>}
    {calendarMode.data?.mode === "google" && <>
      {gmailMode.data?.mode !== "google" && <p className="help" role="alert">Google Calendar requires Gmail Google mode to be enabled for this deployment.</p>}
      {gmailMode.data?.mode === "google" && <>
        {calendarConnected
          ? <p className="help">Google Calendar is connected for this assistant.</p>
          : <p className="help" role="alert">Calendar is disconnected. Connect Google Calendar to enable live booking.</p>}
        {!calendarConnected && <button className="button-primary" type="button" disabled={startCalendarOAuth.isPending} onClick={() => startCalendarOAuth.mutate()}>
          {startCalendarOAuth.isPending ? "Connecting…" : "Connect Google Calendar"}
        </button>}
      </>}
    </>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
