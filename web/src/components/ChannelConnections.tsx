import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "../api/client";
import { useGmailMode, useGoogleFeatures, useGoogleOAuth } from "./channelApi";

export function CalendarConnection({ assistantId, saveDraft }: { assistantId: string; saveDraft: () => Promise<boolean> }) {
  const [error, setError] = useState("");
  const [savingDraft, setSavingDraft] = useState(false);
  const gmailMode = useGmailMode();
  const calendarMode = useQuery({ queryKey: ["calendar-mode"], queryFn: () => unwrap(api.GET("/calendar/mode")) });
  const googleFeatures = useGoogleFeatures(assistantId);
  const startCalendarOAuth = useGoogleOAuth(assistantId, ["calendar"], setError);
  const calendarConnected = googleFeatures.data?.features.includes("calendar") ?? false;
  const connectCalendar = async () => {
    setSavingDraft(true);
    try {
      if (!(await saveDraft())) return;
      startCalendarOAuth.mutate();
    } finally {
      setSavingDraft(false);
    }
  };
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
        {!calendarConnected && <button className="button-primary" type="button" disabled={savingDraft || startCalendarOAuth.isPending} onClick={() => { void connectCalendar(); }}>
          {savingDraft ? "Saving changes…" : startCalendarOAuth.isPending ? "Connecting…" : "Connect Google Calendar"}
        </button>}
      </>}
    </>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
