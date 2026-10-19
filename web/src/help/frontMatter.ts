export type ArticleFrontMatter = {
  title: string;
  category: string;
  order: number;
  summary: string;
  related?: string[];
};

const requiredFields = ["title", "category", "order", "summary"] as const;

export function parseFrontMatter(markdown: string): { frontMatter: ArticleFrontMatter; body: string } {
  const match = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(markdown);
  if (!match) throw new Error("Article must start with a front matter block.");

  const values: Record<string, string | string[]> = {};
  for (const line of match[1].split(/\r?\n/)) {
    const field = /^([a-z][a-z0-9_-]*):\s*(.*)$/.exec(line);
    if (!field) throw new Error(`Invalid front matter line: ${line}`);
    const [, key, value] = field;
    if (key === "related") {
      if (!value.startsWith("[") || !value.endsWith("]")) {
        throw new Error("The related field must be an inline list.");
      }
      const items = value.slice(1, -1).trim();
      values[key] = items ? items.split(",").map((item) => item.trim()).filter(Boolean) : [];
    } else {
      values[key] = value.trim();
    }
  }

  for (const field of requiredFields) {
    if (!values[field]) throw new Error(`Missing required front matter field: ${field}`);
  }

  const order = Number(values.order);
  if (!Number.isFinite(order)) throw new Error("The order field must be a number.");
  if (typeof values.title !== "string" || typeof values.category !== "string" || typeof values.summary !== "string") {
    throw new Error("The title, category, and summary fields must be text.");
  }

  return {
    frontMatter: {
      title: values.title,
      category: values.category,
      order,
      summary: values.summary,
      ...(Array.isArray(values.related) ? { related: values.related } : {}),
    },
    body: markdown.slice(match[0].length).trim(),
  };
}
