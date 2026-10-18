import { Field, type SettingsTabProps } from "./fields";

export function RulesTab({ config, update }: SettingsTabProps) {
  return <>
    {/* Rule presets (FR-S13) render here. */}
    <section className="panel stack" aria-labelledby="policies-heading"><h2 id="policies-heading">Policies</h2>
      <p className="help">Plain-language rules the assistant always follows, in priority order.</p>
      {config.policies.map((policy, index) => <div className="inline-editor" key={`policy-${index}`}>
        <Field path={`policies.${index}`} label={`Policy ${index + 1}`}>{(fieldId) => <textarea id={fieldId} value={policy} onChange={(event) => update((draft) => { draft.policies[index] = event.target.value; })} />}</Field>
        <div className="button-row">
          <button type="button" onClick={() => update((draft) => { [draft.policies[index - 1], draft.policies[index]] = [draft.policies[index], draft.policies[index - 1]]; })} disabled={index === 0}>Move up</button>
          <button type="button" onClick={() => update((draft) => { [draft.policies[index + 1], draft.policies[index]] = [draft.policies[index], draft.policies[index + 1]]; })} disabled={index === config.policies.length - 1}>Move down</button>
          <button type="button" onClick={() => update((draft) => { draft.policies.splice(index, 1); })}>Remove policy</button>
        </div>
      </div>)}
      <button type="button" onClick={() => update((draft) => { draft.policies.push(""); })}>Add policy</button>
    </section>
  </>;
}
