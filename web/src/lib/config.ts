import type { Schemas } from "../api/client";

export type AssistantConfig = Schemas["AssistantConfig-Output"];

const fieldTypes = [
  "string",
  "email",
  "phone",
  "date",
  "boolean",
  "integer",
  "number",
  "enum",
  "list",
];

export function validateAssistantConfig(config: AssistantConfig): Record<string, string> {
  const errors: Record<string, string> = {};
  const set = (path: string, message: string) => {
    errors[path] = message;
  };
  if (!config.business_name.trim()) set("business_name", "Enter a business name.");
  if (!config.greeting.trim()) set("greeting", "Enter a greeting.");

  const { office_hours: hours } = config;
  const timePattern = /^(?:[01]\d|2[0-3]):[0-5]\d$/;
  if (!timePattern.test(hours.opens)) set("office_hours.opens", "Enter a valid time.");
  if (!timePattern.test(hours.closes)) set("office_hours.closes", "Enter a valid time.");
  if (timePattern.test(hours.opens) && timePattern.test(hours.closes) && hours.closes <= hours.opens) {
    set("office_hours.closes", "Closing time must be later than opening time.");
  }
  if (hours.days.length === 0) set("office_hours.days", "Choose at least one day.");

  const serviceKeys = Object.keys(config.services);
  serviceKeys.forEach((key) => {
    if (!key.trim()) set(`services.${key}.key`, "Service keys must not be empty.");
  });
  for (const [serviceKey, service] of Object.entries(config.services)) {
    const seen = new Set<string>();
    service.fields.forEach((field, index) => {
      const base = `services.${serviceKey}.fields.${index}`;
      if (!field.name.trim()) set(`${base}.name`, "Enter a field name.");
      else if (!/^[a-z][a-z0-9_]*$/.test(field.name)) {
        set(`${base}.name`, "Use snake_case, starting with a lowercase letter.");
      }
      if (seen.has(field.name)) set(`${base}.name`, "Field names must be unique within this service.");
      seen.add(field.name);
      if (!field.label.trim()) set(`${base}.label`, "Enter a field label.");
      if (!fieldTypes.includes(field.type)) set(`${base}.type`, "Choose a valid field type.");
      if (field.type === "enum" && !(field.choices?.some((choice) => choice.trim()))) {
        set(`${base}.choices`, "Add at least one choice for an enum field.");
      }
      if (field.type !== "enum" && field.choices != null) {
        set(`${base}.choices`, "Choices are only valid for enum fields.");
      }
    });
  }

  config.urgency_rules.forEach((rule, index) => {
    const base = `urgency_rules.${index}`;
    if (rule.type === "keyword") {
      if (!rule.phrases.some((phrase) => phrase.trim())) set(`${base}.phrases`, "Add at least one phrase.");
      if (!rule.reason.trim()) set(`${base}.reason`, "Enter a reason.");
      return;
    }
    if (!config.services[rule.service]) set(`${base}.service`, "Choose an existing service.");
    const field = config.services[rule.service]?.fields.find((candidate) => candidate.name === rule.field);
    if (!field) set(`${base}.field`, "Choose an existing field.");
    else if (field.type !== "date") set(`${base}.field`, "Choose a date field.");
    for (const name of ["add_months", "minus_days", "urgent_within_days"] as const) {
      if (!Number.isInteger(rule[name]) || rule[name] < 0) set(`${base}.${name}`, "Enter a whole number greater than or equal to zero.");
    }
    if (!rule.reason.trim()) set(`${base}.reason`, "Enter a reason.");
  });
  if (!Number.isFinite(config.review_confidence_threshold) || config.review_confidence_threshold < 0 || config.review_confidence_threshold > 1) {
    set("review_confidence_threshold", "Choose a value between 0 and 1.");
  }
  if (config.booking) {
    if (!config.booking.calendar_id.trim()) set("booking.calendar_id", "Enter a calendar ID.");
    if (config.booking.appointment_types.length === 0) {
      set("booking.appointment_types", "Add at least one appointment type.");
    }
    const seenAppointmentKeys = new Set<string>();
    config.booking.appointment_types.forEach((appointment, index) => {
      const base = `booking.appointment_types.${index}`;
      if (!/^[a-z0-9_]+$/.test(appointment.key)) {
        set(`${base}.key`, "Use lowercase letters, numbers, and underscores.");
      }
      if (seenAppointmentKeys.has(appointment.key)) {
        set(`${base}.key`, "Appointment type keys must be unique.");
      }
      seenAppointmentKeys.add(appointment.key);
      if (!appointment.label.trim()) set(`${base}.label`, "Enter an appointment label.");
      if (!Number.isInteger(appointment.duration_minutes) || appointment.duration_minutes < 1 || appointment.duration_minutes > 480) {
        set(`${base}.duration_minutes`, "Duration must be between 1 and 480 minutes.");
      }
      if (!appointment.when_to_use.trim()) set(`${base}.when_to_use`, "Describe when to use this appointment type.");
    });
    if (!Number.isInteger(config.booking.min_notice_hours) || config.booking.min_notice_hours < 0) {
      set("booking.min_notice_hours", "Enter a whole number greater than or equal to zero.");
    }
    if (!Number.isInteger(config.booking.horizon_days) || config.booking.horizon_days < 1 || config.booking.horizon_days > 60) {
      set("booking.horizon_days", "Choose between 1 and 60 days.");
    }
    if (!Number.isInteger(config.booking.buffer_minutes) || config.booking.buffer_minutes < 0) {
      set("booking.buffer_minutes", "Enter a whole number greater than or equal to zero.");
    }
    if (!Number.isInteger(config.booking.slot_step_minutes) || config.booking.slot_step_minutes < 5 || config.booking.slot_step_minutes > 240) {
      set("booking.slot_step_minutes", "Choose between 5 and 240 minutes.");
    }
    if (!Number.isInteger(config.booking.max_slots_offered) || config.booking.max_slots_offered < 1 || config.booking.max_slots_offered > 5) {
      set("booking.max_slots_offered", "Choose between 1 and 5 slots.");
    }
  }
  return errors;
}
