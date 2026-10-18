import type { SettingsTabId } from "../../lib/settingsTabs";
import { AbilitiesTab } from "./AbilitiesTab";
import { AdvancedTab } from "./AdvancedTab";
import { ChannelsTab } from "./ChannelsTab";
import type { SettingsTabProps } from "./fields";
import { GeneralTab } from "./GeneralTab";
import { KnowledgeTab } from "./KnowledgeTab";
import { RulesTab } from "./RulesTab";

export function SettingsTabContent({ tab, assistantName, setAssistantName, ...props }: SettingsTabProps & {
  tab: SettingsTabId;
  assistantName: string;
  setAssistantName: (name: string) => void;
}) {
  switch (tab) {
    case "general": return <GeneralTab {...props} assistantName={assistantName} setAssistantName={setAssistantName} />;
    case "rules": return <RulesTab {...props} />;
    case "knowledge": return <KnowledgeTab {...props} />;
    case "abilities": return <AbilitiesTab {...props} />;
    case "channels": return <ChannelsTab {...props} />;
    case "advanced": return <AdvancedTab {...props} />;
  }
}
