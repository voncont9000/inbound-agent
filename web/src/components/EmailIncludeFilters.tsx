import { useEffect, useId, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, unwrap, type Schemas } from "../api/client";
import { describeRules, readIncludeRules, type IncludeRule } from "../lib/channelConnections";

export function EmailIncludeFilters({ channel }: {
  channel: Schemas["ChannelConnectionResponse"];
}) {
  const queryClient = useQueryClient();
  const panelId = useId();
  const savedRules = readIncludeRules(channel.settings);
  const [open, setOpen] = useState(true);
  const [rules, setRules] = useState<IncludeRule[]>(savedRules);
  const [saveMessage, setSaveMessage] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const saveFilters = useMutation({
    mutationFn: (includeRules: IncludeRule[]) => unwrap(api.PATCH(
      "/channels/{channel_id}",
      { params: { path: { channel_id: channel.id } }, body: { include_rules: includeRules } },
    )),
    onSuccess: async () => {
      setSaveMessage({ kind: "success", text: "Filters saved." });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["workspace-channels"] }),
        queryClient.invalidateQueries({ queryKey: ["assistant-channels", channel.assistant_id] }),
      ]);
    },
    onError: (caught) => setSaveMessage({
      kind: "error",
      text: caught instanceof Error ? caught.message : "Unable to save email filters.",
    }),
  });

  useEffect(() => {
    setRules(readIncludeRules(channel.settings));
  }, [channel.id, channel.settings]);

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
