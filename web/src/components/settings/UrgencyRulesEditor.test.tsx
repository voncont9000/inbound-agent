import { useState } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { AssistantConfig } from "../../lib/config";
import { UrgencyRulesEditor } from "./UrgencyRulesEditor";

const initialConfig = {
  services: {
    repair: {
      label: "Repair",
      fields: [
        { name: "termination_date", label: "Termination date", type: "date", required: false, critical: false, description: "" },
      ],
    },
  },
  urgency_rules: [],
} as unknown as AssistantConfig;

const existingRules = [
  { type: "sentence", text: "Urgent if the customer needs help today" },
  { type: "keyword", phrases: ["urgent phrase"], reason: "Keyword reason." },
  { type: "date_deadline", service: "repair", field: "termination_date", add_months: 3, minus_days: 1, urgent_within_days: 28, reason: "Date reason." },
] as unknown as AssistantConfig["urgency_rules"];

function EditorHarness({
  initial = initialConfig,
  errors = {},
}: {
  initial?: AssistantConfig;
  errors?: Record<string, string>;
} = {}) {
  const [config, setConfig] = useState(initial);
  const update = (change: (draft: AssistantConfig) => void) => {
    setConfig((current) => {
      const draft = structuredClone(current);
      change(draft);
      return draft;
    });
  };
  return <>
    <UrgencyRulesEditor config={config} update={update} errors={errors} />
    <output data-testid="config">{JSON.stringify(config)}</output>
  </>;
}

describe("UrgencyRulesEditor", () => {
  it("shows sentence, keyword, and date rules together in one Urgency section", () => {
    render(<EditorHarness initial={{ ...initialConfig, urgency_rules: existingRules }} />);

    const panel = screen.getByRole("region", { name: "Urgency" });
    expect(within(panel).getByLabelText("Urgent if… 1")).toHaveValue("Urgent if the customer needs help today");
    expect(within(panel).getByLabelText("Urgent if… 1")).toHaveAttribute("placeholder", "Urgent if the customer needs help today");
    expect(within(panel).getAllByLabelText("Rule type")).toHaveLength(2);
    expect(within(panel).getByText("More rule types: keywords and date deadlines")).toBeInTheDocument();
    expect(within(panel).getByLabelText("Phrases (comma or newline separated)")).toHaveValue("urgent phrase");
    expect(within(panel).getAllByRole("option", { name: "Keywords" })).toHaveLength(2);
    expect(within(panel).getByLabelText("Date field")).toHaveValue("termination_date");
    expect(within(panel).getByText("Deadline: Termination date + 3 months − 1 day. Urgent when 28 days or fewer remain.")).toBeInTheDocument();
  });

  it("opens structured rules by default only when keyword or date rules exist", () => {
    const { unmount } = render(<EditorHarness initial={{
      ...initialConfig,
      urgency_rules: [{ type: "sentence", text: "Urgent today" }] as AssistantConfig["urgency_rules"],
    }} />);
    let details = screen.getByText("More rule types: keywords and date deadlines").closest("details");
    expect(details).not.toHaveAttribute("open");
    unmount();

    for (const rule of [
      { type: "keyword", phrases: ["help"], reason: "Reason." },
      { type: "date_deadline", service: "repair", field: "termination_date", add_months: 0, minus_days: 0, urgent_within_days: 0, reason: "Date reason." },
    ] as AssistantConfig["urgency_rules"]) {
      const view = render(<EditorHarness initial={{ ...initialConfig, urgency_rules: [rule] }} />);
      details = screen.getByText("More rule types: keywords and date deadlines").closest("details");
      expect(details).toHaveAttribute("open");
      view.unmount();
    }
  });

  it("omits zero-valued adjustments and thresholds from the preview", () => {
    const dateRule = {
      type: "date_deadline",
      service: "repair",
      field: "termination_date",
      add_months: 0,
      minus_days: 0,
      urgent_within_days: 0,
      reason: "Date reason.",
    } as AssistantConfig["urgency_rules"][number];
    render(<EditorHarness initial={{ ...initialConfig, urgency_rules: [dateRule] }} />);

    expect(screen.getByText("Deadline: Termination date.")).toBeInTheDocument();
    expect(screen.queryByText(/0 months|0 days/)).not.toBeInTheDocument();
  });

  it("preserves rule order and index paths when rules are added and removed", async () => {
    const user = userEvent.setup();
    render(<EditorHarness initial={{ ...initialConfig, urgency_rules: existingRules }} />);
    const monthsInput = screen.getByLabelText("Months after the date");
    expect(monthsInput).toHaveAttribute("id", "field-urgency_rules.2.add_months");

    await user.click(screen.getByRole("button", { name: "Add urgent rule" }));
    expect(JSON.parse(screen.getByTestId("config").textContent ?? "").urgency_rules.map(({ type }: { type: string }) => type))
      .toEqual(["sentence", "keyword", "date_deadline", "sentence"]);

    await user.click(screen.getAllByRole("button", { name: "Remove urgency rule" })[0]);
    const updated = JSON.parse(screen.getByTestId("config").textContent ?? "");
    expect(updated.urgency_rules.map(({ type }: { type: string }) => type))
      .toEqual(["sentence", "date_deadline", "sentence"]);
    expect(screen.getByLabelText("Months after the date")).toHaveAttribute("id", "field-urgency_rules.1.add_months");
  });
});
