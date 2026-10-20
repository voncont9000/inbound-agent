import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap, type Schemas } from "../api/client";
import type { AssistantConfig } from "../lib/config";
import { keyFromLabel } from "../lib/keys";
import { isGoogleSheetsUrl, parseGoogleSheetId } from "../lib/sheets";
import { templateVariableOptions, type TemplateVariableOption } from "../lib/templateVariables";
import { ErrorState, LoadingState } from "./States";
import { TemplateInput } from "./TemplateInput";

type PostAction = NonNullable<AssistantConfig["post_actions"]>[number];
type ActionKind = PostAction["kind"];

const events = [
  "conversation_completed",
  "urgent",
  "callback_requested",
  "needs_human",
  "complaint",
] as const;
const channels = ["phone", "web", "gmail", "whatsapp"] as const;

const actionTitles: Record<ActionKind, string> = {
  email_customer: "Email the customer",
  notify_staff: "Notify your team",
  sheets_row: "Add a Google Sheets row",
  webhook: "Send a webhook",
};

const actionDescriptions: Record<ActionKind, string> = {
  email_customer: "Send the customer a follow-up email.",
  notify_staff: "Send a notification to your team.",
  sheets_row: "Add conversation details to a spreadsheet.",
  webhook: "Send structured data to another service.",
};

const eventLabels: Record<(typeof events)[number], string> = {
  conversation_completed: "Conversation completed",
  urgent: "Urgent",
  callback_requested: "Callback requested",
  needs_human: "Needs a person",
  complaint: "Complaint",
};

const channelLabels: Record<(typeof channels)[number], string> = {
  phone: "Phone",
  web: "Browser test",
  gmail: "Email",
  whatsapp: "WhatsApp",
};

const actionKinds: ActionKind[] = ["email_customer", "notify_staff", "sheets_row", "webhook"];

function actionIcon(kind: ActionKind) {
  const paths: Record<ActionKind, string> = {
    email_customer: "M4 5h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2m18 2-10 7L2 7",
    notify_staff: "M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9m-8 12h4",
    sheets_row: "M4 3h16v18H4zM4 9h16M4 15h16M10 3v18",
    webhook: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM4 6l4 2m8 8 4 2M20 6l-4 2m-8 8-4 2",
  };
  return <svg aria-hidden="true" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d={paths[kind]} /></svg>;
}

function triggerSummary(action: PostAction) {
  return action.trigger.events.length
    ? action.trigger.events.map((eventName) => eventLabels[eventName]).join(", ").toLowerCase()
    : "no events selected";
}

function actionSummary(action: PostAction) {
  if (action.kind === "notify_staff") {
    return `To ${action.to.join(", ") || "no recipients"} · when ${triggerSummary(action)}`;
  }
  if (action.kind === "email_customer") return `When ${triggerSummary(action)}`;
  if (action.kind === "sheets_row") {
    return action.spreadsheet_id ? `Sheet linked · tab ${action.sheet_name || "Sheet1"}` : "No sheet linked yet";
  }
  try {
    return new URL(action.url).hostname || "No URL yet";
  } catch {
    return "No URL yet";
  }
}

function createAction(kind: ActionKind, key: string): PostAction {
  const trigger = { events: ["conversation_completed"] as Array<(typeof events)[number]>, channels: [], services: [] };
  if (kind === "email_customer") return { kind, key, enabled: true, trigger, subject_template: "Thanks for contacting {{business_name}}", body_template: "Hello {{contact.name}},\n\n{{summary}}" };
  if (kind === "notify_staff") return { kind, key, enabled: true, trigger, to: ["team@example.com"], subject_template: "New enquiry from {{contact.name}}", body_template: "{{summary}}\n\n{{intake_table}}" };
  if (kind === "sheets_row") return { kind, key, enabled: true, trigger, spreadsheet_id: "", sheet_name: "Sheet1", columns: [{ header: "Started", value_template: "{{started_at}}" }, { header: "Name", value_template: "{{contact.name}}" }, { header: "Channel", value_template: "{{channel}}" }, { header: "Summary", value_template: "{{summary}}" }] };
  return { kind, key, enabled: true, trigger, url: "https://example.com/webhook" };
}

function SheetsColumnValue({
  id,
  ariaLabel,
  value,
  onChange,
  variables,
}: {
  id: string;
  ariaLabel: string;
  value: string;
  onChange: (value: string) => void;
  variables: TemplateVariableOption[];
}) {
  const [customText, setCustomText] = useState(false);
  const match = /^\{\{([A-Za-z0-9_.]+)\}\}$/.exec(value);
  const customTextValue = value !== "" && !match;
  const showTemplateInput = customTextValue || (customText && !match);

  if (showTemplateInput) {
    return <div className="sheet-column-custom-value">
      <TemplateInput id={id} label="Value" ariaLabel={ariaLabel} value={value} onChange={onChange} variables={variables} />
      <button type="button" className="button-link" onClick={() => {
        setCustomText(false);
        onChange("");
      }}>Choose a variable</button>
    </div>;
  }

  const selectedName = match?.[1] ?? "";
  const hasSelectedOption = variables.some((variable) => variable.name === selectedName);
  return <label className="field">
    <span>Value</span>
    <select aria-label={ariaLabel} value={selectedName} onChange={(event) => {
      if (event.target.value === "__custom_text__") {
        setCustomText(true);
        onChange("");
      } else if (event.target.value) {
        setCustomText(false);
        onChange(`{{${event.target.value}}}`);
      }
    }}>
      <option value="">Choose a variable</option>
      {selectedName && !hasSelectedOption && <option value={selectedName}>{selectedName}</option>}
      {variables.map((variable) => <option key={variable.name} value={variable.name}>{variable.label}</option>)}
      <option value="__custom_text__">Custom text…</option>
    </select>
  </label>;
}

function GoogleSheetLinkField({
  id,
  index,
  value,
  onChange,
}: {
  id: string;
  index: number;
  value: string;
  onChange: (value: string) => void;
}) {
  const [rawValue, setRawValue] = useState(value);
  const previousValue = useRef(value);
  const parsedId = parseGoogleSheetId(rawValue);
  const hasInvalidValue = Boolean(rawValue.trim()) && !parsedId;
  const helpId = `${id}-help`;
  const errorId = `${id}-error`;

  useEffect(() => {
    if (value === previousValue.current) return;
    previousValue.current = value;
    if (parseGoogleSheetId(rawValue) !== value) setRawValue(value);
  }, [rawValue, value]);

  return <div className="field">
    <label htmlFor={id}>Google Sheet link</label>
    <input
      id={id}
      data-action-first-field
      aria-label={`Google Sheet link ${index + 1}`}
      aria-invalid={hasInvalidValue}
      aria-describedby={hasInvalidValue ? `${helpId} ${errorId}` : helpId}
      value={rawValue}
      onBlur={() => {
        if (isGoogleSheetsUrl(rawValue)) return;
        const spreadsheetId = parseGoogleSheetId(rawValue);
        if (spreadsheetId) onChange(spreadsheetId);
      }}
      onChange={(event) => {
        const nextValue = event.target.value;
        setRawValue(nextValue);
        const spreadsheetId = parseGoogleSheetId(nextValue);
        if (spreadsheetId && isGoogleSheetsUrl(nextValue)) onChange(spreadsheetId);
      }}
    />
    <span id={helpId} className="help">Paste the link from your browser. The connected Google account needs edit access.</span>
    {hasInvalidValue && <span id={errorId} className="field-error" role="alert">Enter a valid Google Sheets link or spreadsheet ID.</span>}
    {parsedId && <a className="button-link post-action-open-sheet" href={`https://docs.google.com/spreadsheets/d/${parsedId}/edit`} target="_blank" rel="noreferrer">Open sheet</a>}
  </div>;
}

export function PostActionsEditor({
  assistantId,
  config,
  update,
}: {
  assistantId: string;
  config: AssistantConfig;
  update: (change: (draft: AssistantConfig) => void) => void;
}) {
  const queryClient = useQueryClient();
  const [results, setResults] = useState<Record<string, Schemas["PostActionTestResponse"]>>({});
  const [generated, setGenerated] = useState<Record<string, string>>({});
  const [expandedActionKey, setExpandedActionKey] = useState<string | null>(null);
  const [showActionPicker, setShowActionPicker] = useState(false);
  const [pendingFocusKey, setPendingFocusKey] = useState<string | null>(null);
  const actionCardRefs = useRef<Record<string, HTMLElement | null>>({});
  const addActionButtonRef = useRef<HTMLButtonElement>(null);
  const actions = config.post_actions ?? [];
  const variables = useQuery({
    queryKey: ["post-action-variables"],
    queryFn: () => unwrap(api.GET("/post-actions/variables")),
  });
  const variableOptions = templateVariableOptions(variables.data ?? [], config.services);
  const channelsQuery = useQuery({
    queryKey: ["assistant-channels", assistantId],
    queryFn: () => unwrap(api.GET("/assistants/{assistant_id}/channels", { params: { path: { assistant_id: assistantId } } })),
  });
  const secrets = useQuery({
    queryKey: ["post-action-secrets", assistantId],
    queryFn: () => unwrap(api.GET("/assistants/{assistant_id}/post-actions/webhook-secrets", { params: { path: { assistant_id: assistantId } } })),
  });
  const testAction = useMutation({
    mutationFn: (action: PostAction) => unwrap(api.POST("/assistants/{assistant_id}/post-actions/test", {
      params: { path: { assistant_id: assistantId } },
      body: { action, conversation_id: null },
    })),
    onSuccess: (result, action) => setResults((current) => ({ ...current, [action.key]: result })),
  });
  const connectSheets = useMutation({
    mutationFn: () => unwrap(api.POST("/assistants/{assistant_id}/channels/gmail/oauth-start", {
      params: { path: { assistant_id: assistantId } },
      body: { features: ["sheets"] },
    })),
    onSuccess: ({ url }) => window.location.assign(url),
  });
  const generateSecret = useMutation({
    mutationFn: (actionKey: string) => unwrap(api.POST("/assistants/{assistant_id}/post-actions/{action_key}/webhook-secret", {
      params: { path: { assistant_id: assistantId, action_key: actionKey } },
    })),
    onSuccess: async ({ action_key, secret }) => {
      setGenerated((current) => ({ ...current, [action_key]: secret }));
      await queryClient.invalidateQueries({ queryKey: ["post-action-secrets", assistantId] });
    },
  });
  const needsSheets = actions.some((action) => action.kind === "sheets_row");
  const gmailConnection = channelsQuery.data?.items.find((channel) => channel.kind === "gmail");
  const shouldConnectSheets = needsSheets && !gmailConnection?.google_features.includes("sheets");

  useEffect(() => {
    if (!pendingFocusKey) return;
    const card = actionCardRefs.current[pendingFocusKey];
    if (!card) return;
    card.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
    card.querySelector<HTMLElement>("[data-action-first-field]")?.focus();
    setPendingFocusKey(null);
  }, [actions.length, pendingFocusKey]);

  useEffect(() => {
    if (!showActionPicker || actions.length === 0) return;
    const dismissOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setShowActionPicker(false);
      window.requestAnimationFrame(() => addActionButtonRef.current?.focus());
    };
    window.addEventListener("keydown", dismissOnEscape);
    return () => window.removeEventListener("keydown", dismissOnEscape);
  }, [actions.length, showActionPicker]);

  const setAction = (index: number, change: (action: PostAction) => PostAction) => {
    update((draft) => {
      const postActions = (draft.post_actions ??= []);
      const current = postActions[index];
      if (current) postActions[index] = change(current);
    });
  };

  const addAction = (kind: ActionKind) => {
    const action = createAction(kind, keyFromLabel(kind, actions.map(({ key }) => key), "action"));
    update((draft) => {
      const postActions = (draft.post_actions ??= []);
      postActions.push(action);
    });
    setExpandedActionKey(action.key);
    setShowActionPicker(false);
    setPendingFocusKey(action.key);
  };

  const actionPicker = <div className="post-action-picker">
    <h3>Choose an action</h3>
    <div className="post-action-picker-grid">
      {actionKinds.map((kind) => <button className="post-action-picker-card" type="button" key={kind} onClick={() => addAction(kind)}>
        {actionIcon(kind)}
        <span className="post-action-picker-copy"><strong>{actionTitles[kind]}</strong><span>{actionDescriptions[kind]}</span></span>
      </button>)}
    </div>
    {actions.length > 0 && <button type="button" className="button-secondary post-action-picker-cancel" onClick={() => {
      setShowActionPicker(false);
      window.requestAnimationFrame(() => addActionButtonRef.current?.focus());
    }}>Cancel</button>}
  </div>;

  return <section className="stack" aria-label="After-conversation actions">
    <p className="muted">Actions run after a conversation is completed. Browser test calls trigger them too; emails are marked [Test].</p>
    {actions.length === 0 && actionPicker}
    {shouldConnectSheets && <div className="stack">
      <p className="help">Connect Google Sheets to enable sheet actions.</p>
      <button className="button-primary" type="button" disabled={connectSheets.isPending} onClick={() => connectSheets.mutate()}>
        {connectSheets.isPending ? "Connecting…" : "Connect Google Sheets"}
      </button>
      {connectSheets.error && <p role="alert">{connectSheets.error.message}</p>}
    </div>}
    {variables.isLoading && <LoadingState />}
    {variables.error && <ErrorState message="Template variables could not be loaded." />}
    {actions.map((action, index) => {
      const expanded = expandedActionKey === action.key;
      const settingsId = `post-action-settings-${index}`;
      const title = actionTitles[action.kind];
      return <article
        className={`inline-editor post-action-card${expanded ? " post-action-card-expanded" : ""}`}
        key={`${action.key}-${index}`}
        ref={(element) => { actionCardRefs.current[action.key] = element; }}
      >
        <div className="post-action-card-header">
          <button
            type="button"
            className="post-action-card-toggle"
            aria-expanded={expanded}
            aria-controls={settingsId}
            aria-label={`${expanded ? "Collapse" : "Expand"} ${title} settings`}
            onClick={() => setExpandedActionKey((current) => current === action.key ? null : action.key)}
          >
            <span className="post-action-card-copy"><strong>{title}</strong><span className="muted">{actionSummary(action)}</span></span>
            <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={expanded ? "m6 15 6-6 6 6" : "m6 9 6 6 6-6"} /></svg>
          </button>
          <label className="switch post-action-enabled">
            <input type="checkbox" role="switch" aria-checked={action.enabled} aria-label={`Enabled ${title}`} checked={action.enabled} onChange={(event) => setAction(index, (current) => ({ ...current, enabled: event.target.checked }))} />
            <span aria-hidden="true">{action.enabled ? "On" : "Off"}</span>
          </label>
          <button type="button" className="button-secondary post-action-remove" aria-label={`Remove ${title}`} onClick={() => {
            update((draft) => { (draft.post_actions ??= []).splice(index, 1); });
            setExpandedActionKey((current) => current === action.key ? null : current);
          }}>Remove</button>
        </div>
        <div id={settingsId} className="post-action-settings" hidden={!expanded}>
          {expanded && <>
            <fieldset><legend>Trigger events</legend><div className="button-row">
              {events.map((eventName) => <label className="check-row" key={eventName}><input type="checkbox" checked={action.trigger.events.includes(eventName)} onChange={(event) => setAction(index, (current) => ({ ...current, trigger: { ...current.trigger, events: event.target.checked ? [...current.trigger.events, eventName] : current.trigger.events.filter((item) => item !== eventName) } }))} />{eventLabels[eventName]}</label>)}
            </div></fieldset>
            <fieldset><legend>Channels (none means all)</legend><div className="button-row">
              {channels.map((channel) => <label className="check-row" key={channel}><input type="checkbox" checked={(action.trigger.channels ?? []).includes(channel)} onChange={(event) => setAction(index, (current) => {
                const currentChannels = current.trigger.channels ?? [];
                return { ...current, trigger: { ...current.trigger, channels: event.target.checked ? [...currentChannels, channel] : currentChannels.filter((item) => item !== channel) } };
              })} />{channelLabels[channel]}</label>)}
            </div></fieldset>
            <fieldset><legend>Services (none means all)</legend><div className="button-row">
              {Object.entries(config.services).map(([key, service]) => <label className="check-row" key={key}><input type="checkbox" checked={(action.trigger.services ?? []).includes(key)} onChange={(event) => setAction(index, (current) => {
                const currentServices = current.trigger.services ?? [];
                return { ...current, trigger: { ...current.trigger, services: event.target.checked ? [...currentServices, key] : currentServices.filter((item) => item !== key) } };
              })} />{service.label || key}</label>)}
            </div></fieldset>
            {(action.kind === "email_customer" || action.kind === "notify_staff") && <>
              {action.kind === "notify_staff" && <label className="field">Staff recipients<input data-action-first-field aria-label={`Staff recipients ${index + 1}`} value={action.to.join(", ")} onChange={(event) => setAction(index, (current) => current.kind === "notify_staff" ? { ...current, to: event.target.value.split(",").map((email) => email.trim()).filter(Boolean) } : current)} /></label>}
              <TemplateInput id={`post-action-subject-${index}`} label="Subject template" ariaLabel={`Subject template ${index + 1}`} value={action.subject_template} variables={variableOptions} firstField={action.kind === "email_customer"} onChange={(value) => setAction(index, (current) => current.kind === "email_customer" || current.kind === "notify_staff" ? { ...current, subject_template: value } : current)} />
              <TemplateInput id={`post-action-body-${index}`} label="Body template" ariaLabel={`Body template ${index + 1}`} value={action.body_template} variables={variableOptions} multiline onChange={(value) => setAction(index, (current) => current.kind === "email_customer" || current.kind === "notify_staff" ? { ...current, body_template: value } : current)} />
            </>}
            {action.kind === "sheets_row" && <>
              <GoogleSheetLinkField id={`post-action-sheet-link-${index}`} index={index} value={action.spreadsheet_id} onChange={(spreadsheetId) => setAction(index, (current) => current.kind === "sheets_row" ? { ...current, spreadsheet_id: spreadsheetId } : current)} />
              <label className="field">Tab name<input aria-label={`Tab name ${index + 1}`} value={action.sheet_name} onChange={(event) => setAction(index, (current) => current.kind === "sheets_row" ? { ...current, sheet_name: event.target.value } : current)} /><span className="help">Defaults to Sheet1</span></label>
              {action.columns.map((column, columnIndex) => <div className="post-action-column-row" key={`column-${columnIndex}`}>
                <label className="field">Column header<input aria-label={`Column header ${index + 1}-${columnIndex + 1}`} value={column.header} onChange={(event) => setAction(index, (current) => current.kind === "sheets_row" ? { ...current, columns: current.columns.map((item, itemIndex) => itemIndex === columnIndex ? { ...item, header: event.target.value } : item) } : current)} /></label>
                <SheetsColumnValue id={`post-action-column-${index}-${columnIndex}`} ariaLabel={`Column value ${index + 1}-${columnIndex + 1}`} value={column.value_template} variables={variableOptions} onChange={(value) => setAction(index, (current) => current.kind === "sheets_row" ? { ...current, columns: current.columns.map((item, itemIndex) => itemIndex === columnIndex ? { ...item, value_template: value } : item) } : current)} />
                <button type="button" className="post-action-column-remove" aria-label="Remove column" onClick={() => setAction(index, (current) => current.kind === "sheets_row" ? { ...current, columns: current.columns.filter((_, itemIndex) => itemIndex !== columnIndex) } : current)}>Remove</button>
              </div>)}
              <button type="button" className="post-action-add-column" onClick={() => setAction(index, (current) => current.kind === "sheets_row" ? { ...current, columns: [...current.columns, { header: "", value_template: "" }] } : current)}>Add column</button>
            </>}
            {action.kind === "webhook" && <>
              <label className="field">Webhook URL<input data-action-first-field aria-label={`Webhook URL ${index + 1}`} value={action.url} onChange={(event) => setAction(index, (current) => current.kind === "webhook" ? { ...current, url: event.target.value } : current)} /></label>
              <p>{secrets.data?.items.some((secret) => secret.action_key === action.key) ? "Signing secret configured." : "No signing secret configured."}</p>
              <button type="button" disabled={generateSecret.isPending} onClick={() => generateSecret.mutate(action.key)}>Generate signing secret</button>
              {generated[action.key] && <label className="field">Copy this secret now<input readOnly value={generated[action.key]} onFocus={(event) => event.currentTarget.select()} /></label>}
              {generateSecret.error && <p role="alert">{generateSecret.error.message}</p>}
            </>}
            <div className="button-row"><button type="button" disabled={testAction.isPending} onClick={() => testAction.mutate(action)}>
              {testAction.isPending ? "Testing…" : "Send test"}
            </button></div>
            {testAction.error && <p role="alert">{testAction.error.message}</p>}
            {results[action.key] && <div className="stack"><p>{results[action.key].would_skip ?? "Ready to send."}</p><pre>{JSON.stringify(results[action.key].rendered, null, 2)}</pre></div>}
          </>}
        </div>
      </article>;
    })}
    {actions.length > 0 && <div className="post-action-add-area">
      <button ref={addActionButtonRef} hidden={showActionPicker} type="button" className="button-secondary post-action-add" aria-expanded={showActionPicker} aria-controls="post-action-picker" onClick={() => setShowActionPicker(true)}>Add another action</button>
      {showActionPicker && <div id="post-action-picker">{actionPicker}</div>}
    </div>}
  </section>;
}
