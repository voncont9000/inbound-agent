// @vitest-environment node
import { expect, it } from "vitest";
import { server } from "../mocks/server";

it("uploads knowledge files with a browser-generated multipart content type", async () => {
  Object.defineProperty(globalThis, "window", {
    value: { location: { origin: "http://localhost:3000" } },
    configurable: true,
  });
  const { uploadKnowledgeFile } = await import("./client");
  let contentType: string | null = null;
  const listener = ({ request }: { request: Request }) => {
    if (request.method === "POST" && request.url.includes("/knowledge/sources")) {
      contentType = request.headers.get("content-type");
    }
  };
  server.events.on("request:start", listener);

  try {
    const result = await uploadKnowledgeFile(
      "20000000-0000-4000-8000-000000000001",
      new File(["manual"], "manual.pdf", { type: "application/pdf" }),
    );
    expect(contentType).toMatch(/^multipart\/form-data;\s*boundary=/i);
    expect(result.kind).toBe("file");
    expect(result.uri).toBe("manual.pdf");
  } finally {
    server.events.removeListener("request:start", listener);
  }
});
