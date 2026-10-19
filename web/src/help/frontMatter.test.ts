import { describe, expect, it } from "vitest";
import { parseFrontMatter } from "./frontMatter";

describe("parseFrontMatter", () => {
  it("parses required fields and an inline related list", () => {
    const parsed = parseFrontMatter(`---
title: Rules
category: assistant-settings
order: 2
summary: Set how your assistant responds.
related: [assistant-settings/knowledge, channels/gmail]
---

## Add a rule
`);
    expect(parsed.frontMatter).toEqual({
      title: "Rules",
      category: "assistant-settings",
      order: 2,
      summary: "Set how your assistant responds.",
      related: ["assistant-settings/knowledge", "channels/gmail"],
    });
    expect(parsed.body).toContain("## Add a rule");
  });

  it.each(["title", "category", "order", "summary"])("requires %s", (field) => {
    const fields = {
      title: "Example",
      category: "getting-started",
      order: "1",
      summary: "A summary.",
    };
    delete fields[field as keyof typeof fields];
    const block = Object.entries(fields).map(([key, value]) => `${key}: ${value}`).join("\n");
    expect(() => parseFrontMatter(`---\n${block}\n---\nArticle body`)).toThrow(`Missing required front matter field: ${field}`);
  });
});
