import { describe, expect, it } from "vitest";
import { articles, categories, getArticle, relatedArticles } from "./articles";
import { settingsTabHelp } from "./settingsTabHelp";

describe("help articles", () => {
  it("loads unique articles in known categories with resolvable related links", () => {
    expect(articles.length).toBeGreaterThanOrEqual(15);
    expect(new Set(articles.map(({ category, slug }) => `${category}/${slug}`)).size).toBe(articles.length);
    for (const article of articles) {
      expect(categories.some(({ id }) => id === article.category)).toBe(true);
      expect(article.slug).toBeTruthy();
      for (const related of article.related ?? []) {
        const [category, slug] = related.split("/");
        expect(getArticle(category, slug), `${related} from ${article.slug}`).toBeDefined();
      }
    }
    for (const path of Object.values(settingsTabHelp)) {
      const [category, slug] = path.split("/");
      expect(getArticle(category, slug), path).toBeDefined();
    }
  });

  it("returns declared related articles or up to three same-category articles", () => {
    const rules = getArticle("assistant-settings", "rules")!;
    expect(relatedArticles(rules).map(({ slug }) => slug)).toEqual(rules.related?.map((path) => path.split("/")[1]));
    const overview = getArticle("assistant-settings", "settings-overview")!;
    expect(relatedArticles(overview).length).toBeLessThanOrEqual(3);
    expect(relatedArticles(overview).every(({ category }) => category === overview.category)).toBe(true);
  });
});
