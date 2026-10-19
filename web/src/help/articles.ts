import { parseFrontMatter } from "./frontMatter";

export const categories = [
  { id: "getting-started", title: "Getting started" },
  { id: "assistant-settings", title: "Assistant settings" },
  { id: "channels", title: "Channels" },
  { id: "abilities", title: "Abilities" },
  { id: "conversations", title: "Conversations and contacts" },
  { id: "account", title: "Account and privacy" },
  { id: "troubleshooting", title: "Troubleshooting" },
] as const;

export type HelpCategoryId = (typeof categories)[number]["id"];
export type HelpArticle = {
  title: string;
  category: HelpCategoryId;
  order: number;
  summary: string;
  related?: string[];
  slug: string;
  body: string;
};

const categoryOrder = new Map(categories.map(({ id }, index) => [id, index]));
const files = import.meta.glob("./articles/**/*.md", { query: "?raw", import: "default", eager: true }) as Record<string, string>;

export const articles: HelpArticle[] = Object.entries(files).map(([path, markdown]) => {
  const [, folder, filename] = /^\.\/articles\/([^/]+)\/([^/]+)\.md$/.exec(path) ?? [];
  const { frontMatter, body } = parseFrontMatter(markdown);
  if (!folder || !filename || !categoryOrder.has(folder as HelpCategoryId)) throw new Error(`Unknown article category folder: ${folder}`);
  if (frontMatter.category !== folder) {
    throw new Error(`Article category "${frontMatter.category}" does not match folder "${folder}" in ${path}.`);
  }
  return {
    ...frontMatter,
    category: frontMatter.category as HelpCategoryId,
    slug: filename,
    body,
  };
}).sort((left, right) =>
  categoryOrder.get(left.category)! - categoryOrder.get(right.category)! || left.order - right.order,
);

export function getArticle(category: string, slug: string): HelpArticle | undefined {
  return articles.find((article) => article.category === category && article.slug === slug);
}

export function relatedArticles(article: HelpArticle): HelpArticle[] {
  if (article.related?.length) {
    return article.related.map((path) => {
      const [category, slug] = path.split("/");
      return getArticle(category, slug);
    }).filter((related): related is HelpArticle => Boolean(related));
  }
  return articles.filter((candidate) => candidate.category === article.category && candidate.slug !== article.slug).slice(0, 3);
}
