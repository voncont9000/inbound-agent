import type { HelpArticle } from "./articles";

export type HelpSearchResult = { article: HelpArticle; snippet: string };

export function bodyAsPlainText(markdown: string): string {
  return markdown
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/<[^>]*>/g, " ")
    .replace(/[#>*_~|-]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function snippetFor(text: string, terms: string[]): string {
  const lower = text.toLowerCase();
  const firstMatch = terms.map((term) => lower.indexOf(term)).filter((index) => index >= 0).sort((a, b) => a - b)[0];
  if (firstMatch === undefined) return text.slice(0, 150);
  const start = Math.max(0, firstMatch - 55);
  const end = Math.min(text.length, firstMatch + 95);
  return `${start > 0 ? "…" : ""}${text.slice(start, end).trim()}${end < text.length ? "…" : ""}`;
}

export function searchArticles(query: string, source: HelpArticle[]): HelpSearchResult[] {
  const terms = query.toLowerCase().trim().split(/\s+/).filter(Boolean);
  if (!terms.length) return [];

  return source.map((article, index) => {
    const title = article.title.toLowerCase();
    const summary = article.summary.toLowerCase();
    const body = bodyAsPlainText(article.body).toLowerCase();
    const fullText = `${title} ${summary} ${body}`;
    if (!terms.every((term) => fullText.includes(term))) return null;
    const score = terms.reduce((total, term) =>
      total + (title.includes(term) ? 100 : summary.includes(term) ? 10 : 1), 0);
    const plainBody = bodyAsPlainText(article.body);
    return { result: { article, snippet: snippetFor(plainBody || article.summary, terms) }, score, index };
  }).filter((entry): entry is { result: HelpSearchResult; score: number; index: number } => Boolean(entry))
    .sort((left, right) => right.score - left.score || left.index - right.index)
    .map(({ result }) => result);
}
