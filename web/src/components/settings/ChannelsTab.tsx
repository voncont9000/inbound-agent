import { Link } from "react-router-dom";
import { Badge } from "../States";
import { ChannelIcon } from "../ChannelIcon";
import { useAssistantChannels } from "../channelApi";
import { channelLabel, channelStatusTone } from "../../lib/channelConnections";
import { Field, type SettingsTabProps } from "./fields";

export function ChannelsTab({ assistantId, config, update }: SettingsTabProps) {
  const channels = useAssistantChannels(assistantId);
  const manageLink = `/channels?assistant=${assistantId}`;
  return <>
    <section className="panel stack" aria-labelledby="assistant-channels-heading">
      <div className="channel-settings-heading">
        <h2 id="assistant-channels-heading">Channels</h2>
        <Link to={manageLink}>Manage channels</Link>
      </div>
      {channels.isLoading && <p className="muted" role="status">Loading channels…</p>}
      {channels.error && <p role="alert">Channel connections could not be loaded.</p>}
      {channels.data && !channels.data.items.length && <p className="muted">No channels connected yet.</p>}
      {!!channels.data?.items.length && <ul className="channel-settings-list">
        {channels.data.items.map((channel) => <li key={channel.id}>
          <ChannelIcon kind={channel.kind} size={18} />
          <strong>{channelLabel(channel.kind)}</strong>
          <span className="channel-settings-id">{channel.external_id}</span>
          <Badge tone={channelStatusTone(channel.status)}>{channel.status.charAt(0).toUpperCase()}{channel.status.slice(1)}</Badge>
        </li>)}
      </ul>}
    </section>
    <section className="panel stack" aria-labelledby="email-triage-heading"><h2 id="email-triage-heading">Email triage</h2>
      <p className="help">How the email agent decides whether to draft a reply, hand the email to a person, or ignore it.</p>
      {([
        ["enquiry", "Enquiry"],
        ["needs_human", "Needs a person"],
        ["spam", "Spam"],
        ["not_relevant", "Not relevant"],
      ] as const).map(([key, label]) => <Field key={key} path={`email_triage.${key}`} label={label}>{(fieldId) => <textarea id={fieldId} value={config.email_triage?.[key] ?? ""} onChange={(event) => update((draft) => { if (draft.email_triage) draft.email_triage[key] = event.target.value; })} />}</Field>)}
    </section>
  </>;
}
