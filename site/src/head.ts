import { SITE_URL } from "./config";
import type { RouteMeta } from "./routes";

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => {
    const entities: Record<string, string> = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return entities[character];
  });
}

export function renderHead(route: RouteMeta): string {
  const title = escapeHtml(route.title);
  const description = escapeHtml(route.description);
  const url = escapeHtml(`${SITE_URL}${route.path === "/" ? "/" : route.path}`);
  const canonical =
    route.path === "/404" ? "" : `<link rel="canonical" href="${url}">`;
  const robots = route.noindex ? '<meta name="robots" content="noindex">' : "";

  return [
    `<title>${title}</title>`,
    `<meta name="description" content="${description}">`,
    canonical,
    '<meta property="og:type" content="website">',
    `<meta property="og:title" content="${title}">`,
    `<meta property="og:description" content="${description}">`,
    `<meta property="og:url" content="${url}">`,
    `<meta property="og:image" content="${SITE_URL}/og.png">`,
    '<meta property="og:site_name" content="Yelvo">',
    '<meta name="twitter:card" content="summary_large_image">',
    robots,
  ]
    .filter(Boolean)
    .join("\n    ");
}
