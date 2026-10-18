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

function EditorHarness() {
  const [config, setConfig] = useState(initialConfig);
  const update = (change: (draft: AssistantConfig) => void) => {
    setConfig((current) => {
      const draft = structuredClone(current);
      change(draft);
      return draft;
    });
  };
  return <>
    <RulePresetsEditor config={config} update={update} />
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

  it("adds pronunciation and sentence urgency entries", async () => {
    const user = userEvent.setup();
    render(<EditorHarness />);

    await user.click(screen.getByRole("button", { name: "Add pronunciation" }));
    await user.type(screen.getByLabelText("Term 1"), "ACAS");
    await user.type(screen.getByLabelText("Say it as 1"), "Ay-cass");
    await user.click(screen.getByRole("button", { name: "Add urgent rule" }));
    await user.type(
      screen.getByLabelText("Urgency condition 1"),
      "Urgent if a deadline is within 4 weeks",
    );

    expect(screen.getByTestId("config")).toHaveTextContent('"term":"ACAS","say_as":"Ay-cass"');
    expect(screen.getByTestId("config")).toHaveTextContent(
      '"type":"sentence","text":"Urgent if a deadline is within 4 weeks"',
    );
  });
});
