import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap, type Schemas } from "../api/client";
import type { AssistantConfig } from "../lib/config";
import { keyFromLabel } from "../lib/keys";
import { ErrorState, LoadingState } from "./States";

type PostAction = NonNullable<AssistantConfig["post_actions"]>[number];
type ActionKind = PostAction["kind"];
type Variable = Schemas["PostActionVariable"];

const events = [
  "conversation_completed",
  "urgent",
  "callback_requested",
  "needs_human",
  "complaint",
] as const;
const channels = ["phone", "web", "gmail", "whatsapp"] as const;

function createAction(kind: ActionKind, key: string): PostAction {
  const trigger = { events: ["conversation_completed"] as Array<(typeof events)[number]>, channels: [], services: [] };
  if (kind === "email_customer") return { kind, key, enabled: true, trigger, subject_template: "Thanks for contacting {{business_name}}", body_template: "Hello {{contact.name}},\n\n{{summary}}" };
  if (kind === "notify_staff") return { kind, key, enabled: true, trigger, to: ["team@example.com"], subject_template: "New enquiry from {{contact.name}}", body_template: "{{summary}}\n\n{{intake_table}}" };
  if (kind === "sheets_row") return { kind, key, enabled: true, trigger, spreadsheet_id: "", sheet_name: "Sheet1", columns: [{ header: "Contact", value_template: "{{contact.name}}" }] };
  return { kind, key, enabled: true, trigger, url: "https://example.com/webhook" };
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
  const actions = config.post_actions ?? [];
  const variables = useQuery({
    queryKey: ["post-action-variables"],
    queryFn: () => unwrap(api.GET("/post-actions/variables")),
  });
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

  const setAction = (index: number, change: (action: PostAction) => PostAction) => {
    update((draft) => {
      const postActions = (draft.post_actions ??= []);
      const current = postActions[index];
      if (current) postActions[index] = change(current);
    });
  };

  return <section className="panel stack" aria-labelledby="post-actions-heading">
    <h2 id="post-actions-heading">After-conversation actions</h2>
    <p className="muted">Actions run after a conversation is completed. Test calls do not trigger them.</p>
    <div className="button-row">
      <label className="field" htmlFor="post-action-kind">Add action</label>
      <select id="post-action-kind" value="" onChange={(event) => {
        if (!event.target.value) return;
        const kind = event.target.value as ActionKind;
        update((draft) => {
          const postActions = (draft.post_actions ??= []);
          postActions.push(createAction(kind, keyFromLabel(kind, postActions.map(({ key }) => key), "action")));
        });
        event.target.value = "";
      }}>
        <option value="">Choose action type</option>
        <option value="email_customer">Email customer</option>
        <option value="notify_staff">Notify staff</option>
        <option value="sheets_row">Add Google Sheets row</option>
        <option value="webhook">Send webhook</option>
      </select>
    </div>
    {shouldConnectSheets && <div className="stack">
      <p className="help">Connect Google Sheets to enable sheet actions.</p>
      <button className="button-primary" type="button" disabled={connectSheets.isPending} onClick={() => connectSheets.mutate()}>
        {connectSheets.isPending ? "Connecting…" : "Connect Google Sheets"}
      </button>
      {connectSheets.error && <p role="alert">{connectSheets.error.message}</p>}
    </div>}
    {variables.isLoading && <LoadingState />}
    {variables.error && <ErrorState message="Template variables could not be loaded." />}
    {actions.map((action, index) => <article className="inline-editor stack" key={`${action.key}-${index}`}>
      <div className="button-row"><h3>{action.kind.replaceAll("_", " ")}</h3><button type="button" onClick={() => update((draft) => { (draft.post_actions ??= []).splice(index, 1); })}>Remove action</button></div>
      <div className="form-grid">
        <label className="check-row"><input type="checkbox" checked={action.enabled} onChange={(event) => setAction(index, (current) => ({ ...current, enabled: event.target.checked }))} />Enabled</label>
      </div>
      <fieldset><legend>Trigger events</legend><div className="button-row">
        {events.map((eventName) => <label className="check-row" key={eventName}><input type="checkbox" checked={action.trigger.events.includes(eventName)} onChange={(event) => setAction(index, (current) => ({ ...current, trigger: { ...current.trigger, events: event.target.checked ? [...current.trigger.events, eventName] : current.trigger.events.filter((item) => item !== eventName) } }))} />{eventName.replaceAll("_", " ")}</label>)}
      </div></fieldset>
      <fieldset><legend>Channels (none means all)</legend><div className="button-row">
        {channels.map((channel) => <label className="check-row" key={channel}><input type="checkbox" checked={(action.trigger.channels ?? []).includes(channel)} onChange={(event) => setAction(index, (current) => {
          const currentChannels = current.trigger.channels ?? [];
          return { ...current, trigger: { ...current.trigger, channels: event.target.checked ? [...currentChannels, channel] : currentChannels.filter((item) => item !== channel) } };
        })} />{channel}</label>)}
      </div></fieldset>
      <fieldset><legend>Services (none means all)</legend><div className="button-row">
        {Object.entries(config.services).map(([key, service]) => <label className="check-row" key={key}><input type="checkbox" checked={(action.trigger.services ?? []).includes(key)} onChange={(event) => setAction(index, (current) => {
          const currentServices = current.trigger.services ?? [];
          return { ...current, trigger: { ...current.trigger, services: event.target.checked ? [...currentServices, key] : currentServices.filter((item) => item !== key) } };
        })} />{service.label || key}</label>)}
      </div></fieldset>
      {(action.kind === "email_customer" || action.kind === "notify_staff") && <>
        {action.kind === "notify_staff" && <label className="field">Staff recipients<input aria-label={`Staff recipients ${index + 1}`} value={action.to.join(", ")} onChange={(event) => setAction(index, (current) => current.kind === "notify_staff" ? { ...current, to: event.target.value.split(",").map((email) => email.trim()).filter(Boolean) } : current)} /></label>}
        <label className="field">Subject template<input aria-label={`Subject template ${index + 1}`} value={action.subject_template} onChange={(event) => setAction(index, (current) => current.kind === "email_customer" || current.kind === "notify_staff" ? { ...current, subject_template: event.target.value } : current)} /></label>
        <label className="field">Body template<textarea aria-label={`Body template ${index + 1}`} value={action.body_template} onChange={(event) => setAction(index, (current) => current.kind === "email_customer" || current.kind === "notify_staff" ? { ...current, body_template: event.target.value } : current)} /></label>
      </>}
      {action.kind === "sheets_row" && <>
        <label className="field">Spreadsheet ID<input aria-label={`Spreadsheet ID ${index + 1}`} value={action.spreadsheet_id} onChange={(event) => setAction(index, (current) => current.kind === "sheets_row" ? { ...current, spreadsheet_id: event.target.value } : current)} /></label>
        <label className="field">Sheet name<input aria-label={`Sheet name ${index + 1}`} value={action.sheet_name} onChange={(event) => setAction(index, (current) => current.kind === "sheets_row" ? { ...current, sheet_name: event.target.value } : current)} /></label>
        {action.columns.map((column, columnIndex) => <div className="form-grid" key={`column-${columnIndex}`}>
          <label className="field">Column header<input aria-label={`Column header ${index + 1}-${columnIndex + 1}`} value={column.header} onChange={(event) => setAction(index, (current) => current.kind === "sheets_row" ? { ...current, columns: current.columns.map((item, itemIndex) => itemIndex === columnIndex ? { ...item, header: event.target.value } : item) } : current)} /></label>
          <label className="field">Column value template<input aria-label={`Column value ${index + 1}-${columnIndex + 1}`} value={column.value_template} onChange={(event) => setAction(index, (current) => current.kind === "sheets_row" ? { ...current, columns: current.columns.map((item, itemIndex) => itemIndex === columnIndex ? { ...item, value_template: event.target.value } : item) } : current)} /></label>
          <button type="button" onClick={() => setAction(index, (current) => current.kind === "sheets_row" ? { ...current, columns: current.columns.filter((_, itemIndex) => itemIndex !== columnIndex) } : current)}>Remove column</button>
        </div>)}
        <button type="button" onClick={() => setAction(index, (current) => current.kind === "sheets_row" ? { ...current, columns: [...current.columns, { header: "", value_template: "" }] } : current)}>Add column</button>
      </>}
      {action.kind === "webhook" && <>
        <label className="field">Webhook URL<input aria-label={`Webhook URL ${index + 1}`} value={action.url} onChange={(event) => setAction(index, (current) => current.kind === "webhook" ? { ...current, url: event.target.value } : current)} /></label>
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
    </article>)}
    <details><summary>Template variables</summary>
      <ul>{(variables.data ?? []).map((variable: Variable) => <li key={variable.name}><code>{`{{${variable.name}}}`}</code> — {variable.description}</li>)}</ul>
    </details>
  </section>;
}
