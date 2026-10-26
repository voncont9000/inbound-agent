import { readFile, access } from "node:fs/promises";
import { resolve } from "node:path";

async function check() {
  const heroSource = await readFile(resolve("src/content/hero.ts"), "utf8");
  const heroMatch = heroSource.match(/HERO_HEADLINE\s*=\s*"([^"]+)"/);
  if (!heroMatch) throw new Error("Could not read HERO_HEADLINE from src/content/hero.ts");

  const homepage = await readFile(resolve("dist/index.html"), "utf8");
  const headline = homepage
    .match(/<h1\b[^>]*>([\s\S]*?)<\/h1>/)?.[1]
    ?.replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!headline?.includes(heroMatch[1])) {
    throw new Error("dist/index.html does not contain the prerendered hero headline");
  }

  for (const href of [
    'href="#product"',
    'href="#use-cases"',
    'href="#integrations"',
    'href="#faq"',
    'href="/help"',
    'href="/login"',
    'href="/signup"',
  ]) {
    if (!homepage.includes(href)) throw new Error(`dist/index.html is missing nav link ${href}`);
  }

  for (const file of ["dist/privacy/index.html", "dist/imprint/index.html"]) {
    const html = await readFile(resolve(file), "utf8");
    if (!html.includes('<meta name="robots" content="noindex">')) {
      throw new Error(`${file} is missing its noindex metadata`);
    }
  }

  for (const file of [
    "dist/index.html",
    "dist/privacy/index.html",
    "dist/imprint/index.html",
    "dist/404.html",
  ]) {
    await access(resolve(file));
  }
}

check().catch((error) => {
  console.error(`Prerender check failed: ${error.message}`);
  process.exitCode = 1;
});
