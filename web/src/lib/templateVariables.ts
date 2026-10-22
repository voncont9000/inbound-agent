import type { Schemas } from "../api/client";
import type { AssistantConfig } from "./config";

export type TemplateVariableOption = {
  name: string;
  label: string;
  description: string;
};

const friendlyLabels: Record<string, string> = {
  business_name: "Business name",
  "contact.name": "Customer name",
  "contact.first_name": "Customer first name",
  "contact.email": "Customer email",
  "contact.phone": "Customer phone",
  summary: "Conversation summary",
  customer_recap: "Customer recap",
  next_step: "Next step",
  started_at: "Conversation start time",
  conversation_id: "Conversation ID",
  channel: "Channel",
  service: "Service",
  service_key: "Service key",
  test_call: "Browser test call",
  intake_table: "Intake details",
};

function readableName(name: string) {
  return name
    .split(/[._]/)
    .filter(Boolean)
    .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
    .join(" ");
}

export function templateVariableOptions(
  apiVariables: readonly Schemas["PostActionVariable"][],
  services: AssistantConfig["services"],
): TemplateVariableOption[] {
  const options = new Map<string, TemplateVariableOption>();
  for (const variable of apiVariables) {
    options.set(variable.name, {
      name: variable.name,
      label: friendlyLabels[variable.name] ?? readableName(variable.name),
      description: variable.description,
    });
  }
  for (const service of Object.values(services)) {
    for (const field of service.fields) {
      const name = `intake.${field.name}`;
      if (!options.has(name)) {
        options.set(name, {
          name,
          label: field.label || readableName(name),
          description: field.description || "Intake field",
        });
      }
    }
  }
  return [...options.values()];
}

export function slugVariableName(value: string) {
  return value.trim().toLowerCase().replace(/\s+/g, "_").replace(/[^a-z0-9_.]/g, "");
}
