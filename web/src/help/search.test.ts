import { describe, expect, it } from "vitest";
import type { HelpArticle } from "./articles";
import { searchArticles } from "./search";

const article = (slug: string, title: string, summary: string, body: string): HelpArticle => ({
  slug,
  title,
  category: "getting-started",
  order: 1,
  summary,
  body,
});

describe("searchArticles", () => {
  const source = [
    article("body-match", "Using your inbox", "A guide to messages.", "Connect Gmail to organise inbound mail."),
    article("title-match", "Gmail setup", "A guide to messages.", "Connect Gmail to organise your mailbox."),
    article("other", "Calls and language", "How to use phone calls.", "Choose a voice."),
  ];

  it("ranks title matches above body matches", () => {
    expect(searchArticles("gmail", source).map(({ article: found }) => found.slug)).toEqual(["title-match", "body-match"]);
  });

  it("requires every query term, ignores case and returns body snippets", () => {
    expect(searchArticles("gmail mailbox", source).map(({ article: found }) => found.slug)).toEqual(["title-match"]);
    const result = searchArticles("CONNECT gmail", source);
    expect(result[0].snippet.toLowerCase()).toContain("gmail");
  });

  it("returns no results for an empty query", () => {
    expect(searchArticles("  ", source)).toEqual([]);
  });
});
