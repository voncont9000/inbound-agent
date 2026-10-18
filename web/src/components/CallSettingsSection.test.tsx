import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { CallRecordingPlayer } from "./CallRecordingPlayer";
import { CallSettingsSection } from "./CallSettingsSection";
import { tenantFixtures } from "../mocks/fixtures";
import type { AssistantConfig } from "../lib/config";

describe("CallSettingsSection", () => {
  it("updates call and recording settings", () => {
    function Harness() {
      const [config, setConfig] = useState(
        structuredClone(tenantFixtures[0].assistants[0].config) as AssistantConfig,
      );
      const update = (change: (draft: AssistantConfig) => void) => {
        setConfig((current) => {
          const draft = structuredClone(current);
          change(draft);
          return draft;
        });
      };
      return <CallSettingsSection config={config} update={update} errors={{}} />;
    }
    render(<Harness />);

    fireEvent.change(screen.getByLabelText("Still-there prompt"), { target: { value: "Are you with me?" } });
    expect(screen.getByLabelText("Still-there prompt")).toHaveValue("Are you with me?");

    fireEvent.click(screen.getByRole("checkbox", { name: /Record calls/ }));
    expect(screen.getByLabelText("Retention (days)")).toBeTruthy();
  });
});

describe("CallRecordingPlayer", () => {
  it("renders only when recording metadata is present", () => {
    const { container, rerender } = render(<CallRecordingPlayer conversationId="conversation-1" recording={null} />);
    expect(container.querySelector("audio")).toBeNull();

    rerender(<CallRecordingPlayer
      conversationId="conversation-1"
      recording={{ duration_seconds: 65, size_bytes: 1000, created_at: "2026-02-01T10:30:00Z", expires_at: "2026-03-03T10:30:00Z", is_test: true }}
    />);
    const audio = container.querySelector("audio");
    expect(audio?.getAttribute("preload")).toBe("none");
    expect(audio?.getAttribute("src")).toBe(`${window.location.origin}/api/conversations/conversation-1/recording`);
    expect(screen.getByText("Test")).toBeTruthy();
    expect(screen.getByText(/Deleted automatically on/)).toBeTruthy();
  });
});
