import { RulePresetsEditor } from "../RulePresetsEditor";
import type { SettingsTabProps } from "./fields";
import { UrgencyRulesEditor } from "./UrgencyRulesEditor";

export function RulesTab({ config, update, errors }: SettingsTabProps) {
  return <>
    <RulePresetsEditor config={config} update={update} errors={errors} />
    <UrgencyRulesEditor config={config} update={update} errors={errors} />
  </>;
}
