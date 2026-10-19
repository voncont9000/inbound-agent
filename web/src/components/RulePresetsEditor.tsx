import type { AssistantConfig } from "../lib/config";
import { FieldError } from "./States";
import { invalidProps } from "./settings/errors";

type RulesConfig = NonNullable<AssistantConfig["rules"]>;
type Update = (change: (draft: AssistantConfig) => void) => void;

const DEFAULT_RULES: RulesConfig = {
  ai_disclosure: "at_start",
  style: null,
  off_topic: null,
  angry_caller: null,
  sales_calls: null,
  pronunciations: [],
};

function updateRules(update: Update, change: (rules: RulesConfig) => void) {
  update((draft) => {
    const rules = draft.rules ?? structuredClone(DEFAULT_RULES);
    change(rules);
    draft.rules = rules;
  });
}

export function RulePresetsEditor({
  config,
  update,
  errors = {},
}: {
  config: AssistantConfig;
  update: Update;
  errors?: Record<string, string>;
}) {
  const rules = config.rules ?? DEFAULT_RULES;
  const pronunciations = rules.pronunciations ?? [];

  return <section className="panel stack" aria-labelledby="rules-heading">
    <h2 id="rules-heading">Rules</h2>
    <div className="form-grid">
      <label className="field">
        <span>AI disclosure</span>
        <select
          aria-label="AI disclosure"
          value={rules.ai_disclosure}
          onChange={(event) => updateRules(update, (next) => {
            next.ai_disclosure = event.target.value as RulesConfig["ai_disclosure"];
          })}
        >
          <option value="at_start">At the start of every conversation</option>
          <option value="when_asked">Only when asked</option>
        </select>
      </label>
      <label className="field">
        <span>Communication style</span>
        <select
          aria-label="Communication style"
          value={rules.style ?? ""}
          onChange={(event) => updateRules(update, (next) => {
            next.style = (event.target.value || null) as RulesConfig["style"];
          })}
        >
          <option value="">Not set</option>
          <option value="formal">Formal</option>
          <option value="friendly">Friendly</option>
          <option value="neutral">Neutral</option>
        </select>
      </label>
      <label className="field">
        <span>Off-topic questions</span>
        <select
          aria-label="Off-topic questions"
          value={rules.off_topic ?? ""}
          onChange={(event) => updateRules(update, (next) => {
            next.off_topic = (event.target.value || null) as RulesConfig["off_topic"];
          })}
        >
          <option value="">Not set</option>
          <option value="redirect">Politely redirect to the business</option>
          <option value="answer_briefly">Answer briefly, then return to the enquiry</option>
        </select>
      </label>
      <label className="field">
        <span>Angry or upset callers</span>
        <select
          aria-label="Angry or upset callers"
          value={rules.angry_caller ?? ""}
          onChange={(event) => updateRules(update, (next) => {
            next.angry_caller = (event.target.value || null) as RulesConfig["angry_caller"];
          })}
        >
          <option value="">Not set</option>
          <option value="de_escalate">Stay calm and de-escalate</option>
        </select>
        <span className="help">Offers a transfer only when call forwarding is set up.</span>
      </label>
      <label className="field">
        <span>Sales and competitor calls</span>
        <select
          aria-label="Sales and competitor calls"
          value={rules.sales_calls ?? ""}
          onChange={(event) => updateRules(update, (next) => {
            next.sales_calls = (event.target.value || null) as RulesConfig["sales_calls"];
          })}
        >
          <option value="">Not set</option>
          <option value="decline">Decline politely</option>
          <option value="take_message">Take a message for the team</option>
        </select>
      </label>
    </div>

    <div className="stack">
      <h3>Custom rules</h3>
      <p className="help">Plain-language rules the assistant always follows, in priority order.</p>
      {config.policies.map((policy, index) => <div className="inline-editor" key={`policy-${index}`}>
        <label className="field">
          <span>Custom rule {index + 1}</span>
          <textarea
            aria-label={`Custom rule ${index + 1}`}
            value={policy}
            onChange={(event) => update((draft) => { draft.policies[index] = event.target.value; })}
          />
        </label>
        <div className="button-row">
          <button type="button" onClick={() => update((draft) => {
            [draft.policies[index - 1], draft.policies[index]] = [draft.policies[index], draft.policies[index - 1]];
          })} disabled={index === 0}>Move up</button>
          <button type="button" onClick={() => update((draft) => {
            [draft.policies[index + 1], draft.policies[index]] = [draft.policies[index], draft.policies[index + 1]];
          })} disabled={index === config.policies.length - 1}>Move down</button>
          <button type="button" onClick={() => update((draft) => { draft.policies.splice(index, 1); })}>
            Remove custom rule
          </button>
        </div>
      </div>)}
      <button type="button" onClick={() => update((draft) => { draft.policies.push(""); })}>
        Add custom rule
      </button>
    </div>

    <div className="stack">
      <h3>Pronunciation</h3>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Term</th><th>Say it as</th><th>Remove</th></tr></thead>
          <tbody>
            {pronunciations.map((entry, index) => {
              const termPath = `rules.pronunciations.${index}.term`;
              const sayAsPath = `rules.pronunciations.${index}.say_as`;
              return <tr key={`pronunciation-${index}`}>
                <td>
                  <label className="sr-only" htmlFor={`pronunciation-term-${index}`}>Term {index + 1}</label>
                  <input
                    id={`pronunciation-term-${index}`}
                    value={entry.term}
                    {...invalidProps(errors, termPath)}
                    onChange={(event) => updateRules(update, (next) => {
                      (next.pronunciations ??= [])[index].term = event.target.value;
                    })}
                  />
                  <FieldError id={`field-${termPath}-error`}>{errors[termPath]}</FieldError>
                </td>
                <td>
                  <label className="sr-only" htmlFor={`pronunciation-say-as-${index}`}>Say it as {index + 1}</label>
                  <input
                    id={`pronunciation-say-as-${index}`}
                    value={entry.say_as}
                    {...invalidProps(errors, sayAsPath)}
                    onChange={(event) => updateRules(update, (next) => {
                      (next.pronunciations ??= [])[index].say_as = event.target.value;
                    })}
                  />
                  <FieldError id={`field-${sayAsPath}-error`}>{errors[sayAsPath]}</FieldError>
                </td>
                <td><button type="button" onClick={() => updateRules(update, (next) => {
                  next.pronunciations?.splice(index, 1);
                })}>Remove pronunciation</button></td>
              </tr>;
            })}
          </tbody>
        </table>
      </div>
      <button type="button" onClick={() => updateRules(update, (next) => {
        (next.pronunciations ??= []).push({ term: "", say_as: "" });
      })}>Add pronunciation</button>
    </div>

  </section>;
}
