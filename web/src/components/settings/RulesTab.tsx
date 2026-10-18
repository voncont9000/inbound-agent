import { RulePresetsEditor } from "../RulePresetsEditor";
import type { SettingsTabProps } from "./fields";

export function RulesTab({ config, update, errors }: SettingsTabProps) {
  return <RulePresetsEditor config={config} update={update} errors={errors} />;
}
