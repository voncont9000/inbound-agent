import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap, type Schemas } from "../api/client";
import { ErrorState, LoadingState } from "./States";

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

export function ChannelConnections({ assistantId }: { assistantId: string }) {
  const [error, setError] = useState("");
  const gmailMode = useGmailMode();
  const channels = useChannels(assistantId);
  const startGmailOAuth = useGoogleOAuth(assistantId, undefined, setError);
  return <section className="panel stack" aria-labelledby="assistant-channels-heading">
    <h2 id="assistant-channels-heading">Channels</h2>
    <p className="muted">Phone numbers, WhatsApp numbers and Gmail inboxes this assistant answers.</p>
    {channels.isLoading && <LoadingState />}
    {channels.error && <ErrorState message="Channel connections could not be loaded." />}
    {channels.data?.items.map((channel) => <p key={channel.id}>{channel.kind}: <strong>{channel.external_id}</strong></p>)}
    {channels.data && !channels.data.items.length && <p className="muted">No channels connected yet. Add a phone or WhatsApp number under Advanced.</p>}
    {gmailMode.data?.mode === "google" && <div className="stack">
      <p className="help">Gmail is draft-only. Replies are saved as drafts for review and are never sent automatically.</p>
      <button className="button-primary" type="button" disabled={startGmailOAuth.isPending} onClick={() => startGmailOAuth.mutate()}>
        {startGmailOAuth.isPending ? "Connecting…" : "Connect Gmail"}
      </button>
    </div>}
    {gmailMode.data?.mode === "off" && <p className="help">Gmail is not enabled for this deployment.</p>}
    {error && <p role="alert">{error}</p>}
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

export function ManualChannelForm({ assistantId }: { assistantId: string }) {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<Schemas["ChannelConnectionCreate"]["kind"]>("phone");
  const [externalId, setExternalId] = useState("");
  const [error, setError] = useState("");
  const gmailMode = useGmailMode();
  const add = useMutation({
    mutationFn: () => unwrap(api.POST("/assistants/{assistant_id}/channels", {
      params: { path: { assistant_id: assistantId } },
      body: { kind, external_id: externalId, settings: {} },
    })),
    onSuccess: async () => {
      setExternalId("");
      setError("");
      await queryClient.invalidateQueries({ queryKey: ["assistant-channels", assistantId] });
    },
    onError: (caught) => setError(caught instanceof Error ? caught.message : "Unable to add channel."),
  });
  const channels = useChannels(assistantId);
  return <section className="panel stack" aria-labelledby="manual-channel-heading">
    <h2 id="manual-channel-heading">Add a channel manually</h2>
    <p className="help">Connect a phone number, WhatsApp phone number ID, or test inbox by its external ID.</p>
    {channels.data?.items.map((channel) => <p key={channel.id}>{channel.kind}: <strong>{channel.external_id}</strong></p>)}
    {gmailMode.data?.mode !== "off" && (gmailMode.data?.mode !== "google" || kind !== "gmail") && <form className="form-grid" onSubmit={(event) => {
      event.preventDefault();
      event.stopPropagation();
      setError("");
      add.mutate();
    }}>
      <div className="field"><label htmlFor="field-channel-kind">Channel</label><select id="field-channel-kind" value={kind} onChange={(event) => setKind(event.target.value as typeof kind)}>
        <option value="phone">Phone</option><option value="whatsapp">WhatsApp</option>{gmailMode.data?.mode === "fake" && <option value="gmail">Gmail</option>}<option value="web">Web</option>
      </select></div>
      <div className="field"><label htmlFor="field-channel-external-id">{kind === "whatsapp" ? "WhatsApp phone_number_id" : kind === "gmail" ? "Mailbox email address" : "External ID"}</label><input id="field-channel-external-id" value={externalId} onChange={(event) => setExternalId(event.target.value)} required /></div>
      <button className="button-primary" type="submit" disabled={add.isPending || !externalId.trim()}>{add.isPending ? "Adding…" : "Add channel"}</button>
    </form>}
    {kind === "whatsapp" && <p className="help">Enter the phone_number_id from your Meta WhatsApp Business account.</p>}
    {kind === "gmail" && <p className="help">Enter the mailbox email address. Drafts are created for review; mail is never sent automatically.</p>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
