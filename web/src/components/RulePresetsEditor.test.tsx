import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { RulePresetsEditor } from "./RulePresetsEditor";
import type { AssistantConfig } from "../lib/config";

const initialConfig = {
  policies: [],
  rules: {
    ai_disclosure: "at_start",
    style: null,
    off_topic: null,
    angry_caller: null,
    sales_calls: null,
    pronunciations: [],
  },
  urgency_rules: [],
} as unknown as AssistantConfig;

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
    <RulePresetsEditor config={config} update={update} errors={errors} />
    <output data-testid="config">{JSON.stringify(config)}</output>
  </>;
}

describe("RulePresetsEditor", () => {
  it("updates presets and maps Not set to null", async () => {
    const user = userEvent.setup();
    render(<EditorHarness />);

    await user.selectOptions(screen.getByLabelText("Communication style"), "formal");
    expect(screen.getByTestId("config")).toHaveTextContent('"style":"formal"');
    await user.selectOptions(screen.getByLabelText("Communication style"), "");
    expect(screen.getByTestId("config")).toHaveTextContent('"style":null');
  });

  it("adds pronunciation entries", async () => {
    const user = userEvent.setup();
    render(<EditorHarness />);

    await user.click(screen.getByRole("button", { name: "Add pronunciation" }));
    await user.type(screen.getByLabelText("Term 1"), "ACAS");
    await user.type(screen.getByLabelText("Say it as 1"), "Ay-cass");

    expect(screen.getByTestId("config")).toHaveTextContent('"term":"ACAS","say_as":"Ay-cass"');
  });

  it("shows pronunciation validation errors", () => {
    render(
      <EditorHarness
        initial={{
          ...initialConfig,
          rules: {
            ...initialConfig.rules,
            ai_disclosure: initialConfig.rules?.ai_disclosure ?? "at_start",
            pronunciations: [{ term: "", say_as: "" }],
          },
        }}
        errors={{ "rules.pronunciations.0.term": "Enter a term." }}
      />,
    );

    const termInput = screen.getByLabelText("Term 1");
    expect(termInput).toHaveAttribute("aria-invalid", "true");
    expect(termInput).toHaveAttribute(
      "aria-describedby",
      "field-rules.pronunciations.0.term-error",
    );
    expect(screen.getByText("Enter a term.")).toBeInTheDocument();
  });

  it("keeps custom rules in priority order with move controls", async () => {
    const user = userEvent.setup();
    render(<EditorHarness initial={{ ...initialConfig, policies: ["First rule", "Second rule"] } as AssistantConfig} />);

    await user.click(screen.getAllByRole("button", { name: "Move down" })[0]);

    expect(screen.getByTestId("config")).toHaveTextContent('"policies":["Second rule","First rule"]');
  });
});
