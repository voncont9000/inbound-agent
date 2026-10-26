import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const template = await readFile(resolve("dist/index.html"), "utf8");
if (!template.includes("<!--app-head-->") || !template.includes("<!--app-html-->")) {
  throw new Error("dist/index.html is missing an app-head or app-html placeholder");
}

const { render } = await import(pathToFileURL(resolve("dist-ssr/entry-server.js")).href);
const pages = [
  ["/", "dist/index.html"],
  ["/privacy", "dist/privacy/index.html"],
  ["/imprint", "dist/imprint/index.html"],
  ["/404", "dist/404.html"],
];

for (const [path, output] of pages) {
  const { html, head } = render(path);
  const prerendered = template
    .replace("<!--app-head-->", head)
    .replace("<!--app-html-->", html);
  const outputPath = resolve(output);
  await mkdir(dirname(outputPath), { recursive: true });
  await writeFile(outputPath, prerendered);
}
