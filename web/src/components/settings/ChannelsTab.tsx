import { ChannelConnections } from "../ChannelConnections";
import { Field, type SettingsTabProps } from "./fields";

export function ChannelsTab({ assistantId, config, update }: SettingsTabProps) {
  return <>
    <ChannelConnections assistantId={assistantId} />
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
