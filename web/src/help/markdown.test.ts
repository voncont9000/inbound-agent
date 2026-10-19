import { describe, expect, it } from "vitest";
import { renderMarkdown } from "./markdown";

describe("renderMarkdown", () => {
  it("renders headings, lists, links, and code", () => {
    const markdown = [
      "## Set a rule",
      "",
      "1. Open the [Rules tab](/assistants).",
      "2. Use `Save changes`.",
    ].join("\n");
    expect(renderMarkdown(markdown)).toContain("<h2>Set a rule</h2>");
    const html = renderMarkdown(["- First", "- Second", "", "`Save changes`"].join("\n"));
    expect(html).toContain("<ul>");
    expect(html).toContain("<li>First</li>");
    expect(html).toContain("<code>Save changes</code>");
  });
});
