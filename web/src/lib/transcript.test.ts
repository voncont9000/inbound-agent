import { describe, expect, it } from "vitest";
import type { Schemas } from "../api/client";
import { formatDate } from "./date";
import { transcriptRows } from "./transcript";

const startedAt = "2026-02-01T10:30:00.000Z";

function message(
  id: string,
  role: Schemas["MessageResponse"]["role"],
  text: string,
  offsetSeconds: number,
  meta: Schemas["MessageResponse"]["meta"] = {},
): Schemas["MessageResponse"] {
  return {
    id,
    role,
    text,
    ts: new Date(Date.parse(startedAt) + offsetSeconds * 1000).toISOString(),
    meta,
  };
}

describe("transcriptRows", () => {
  it("merges legacy assistant word rows and keeps the first id and timestamp", () => {
    const rows = transcriptRows([
      message("first", "assistant", "Hello ", 0),
      message("second", "assistant", " there", 0.1),
      message("third", "assistant", "today.", 0.2),
    ], "phone", startedAt);

    expect(rows).toEqual([{
      id: "first",
      role: "assistant",
      speaker: "Assistant",
      text: "Hello there today.",
      ts: startedAt,
      time: "0:00",
      draft: false,
    }]);
  });

  it("compares each fragment with the previous fragment and stops after a gap over two seconds", () => {
    const rows = transcriptRows([
      message("first", "assistant", "One", 0),
      message("second", "assistant", "two", 1.5),
      message("third", "assistant", "three", 3),
      message("fourth", "assistant", "Four", 5.001),
    ], "web", startedAt);

    expect(rows.map(({ id, text }) => ({ id, text }))).toEqual([
      { id: "first", text: "One two three" },
      { id: "fourth", text: "Four" },
    ]);
  });

  it("does not merge across roles", () => {
    const rows = transcriptRows([
      message("caller-1", "caller", "Hello", 0),
      message("assistant", "assistant", "Hi", 0.1),
      message("caller-2", "caller", "Thanks", 0.2),
    ], "phone", startedAt);

    expect(rows.map(({ role, text }) => ({ role, text }))).toEqual([
      { role: "caller", text: "Hello" },
      { role: "assistant", text: "Hi" },
      { role: "caller", text: "Thanks" },
    ]);
  });

  it("does not merge messages in Gmail and preserves the existing date format", () => {
    const messages = [
      message("customer-1", "caller", "First email", 0),
      message("assistant", "assistant", "Reply", 0.1, { draft: true }),
      message("customer-2", "caller", "Follow-up", 0.2),
    ];
    const rows = transcriptRows(messages, "gmail", startedAt);

    expect(rows).toHaveLength(3);
    expect(rows[0].time).toBe(formatDate(messages[0].ts));
    expect(rows[1].draft).toBe(true);
  });

  it.each([
    [0, "0:00"],
    [65, "1:05"],
    [3660, "1:01:00"],
    [-10, "0:00"],
  ])("formats a voice offset of %i seconds as %s", (offsetSeconds, expected) => {
    expect(transcriptRows([
      message("row", "caller", "Hello", offsetSeconds),
    ], "phone", startedAt)[0].time).toBe(expected);
  });

  it.each([
    ["phone", "Caller"],
    ["web", "Caller"],
    ["gmail", "Customer"],
    ["whatsapp", "Customer"],
  ])("labels speakers for the %s channel", (channel, customerSpeaker) => {
    const rows = transcriptRows([
      message("customer", "caller", "Hello", 0),
      message("assistant", "assistant", "Hi", 3),
    ], channel, startedAt);

    expect(rows.map(({ speaker }) => speaker)).toEqual([customerSpeaker, "Assistant"]);
  });
});
