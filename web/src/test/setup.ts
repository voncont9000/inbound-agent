import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll } from "vitest";
import { cleanup } from "@testing-library/react";
import { server } from "../mocks/server";
import { resetMockDb } from "../mocks/db";

const assistantPuts = new Set<string>();
server.events.on("request:start", ({ request, requestId }) => {
  if (request.method === "PUT" && new URL(request.url).pathname.includes("/api/assistants/")) {
    assistantPuts.add(requestId);
  }
});
const finishAssistantPut = ({ requestId }: { requestId: string }) => assistantPuts.delete(requestId);
server.events.on("response:mocked", finishAssistantPut);
server.events.on("response:bypass", finishAssistantPut);
server.events.on("request:unhandled", finishAssistantPut);

async function waitForAssistantPuts() {
  const deadline = Date.now() + 2000;
  while (assistantPuts.size) {
    if (Date.now() >= deadline) throw new Error(`Timed out waiting for ${assistantPuts.size} assistant save request(s).`);
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(async () => {
  cleanup();
  await new Promise((resolve) => setTimeout(resolve, 0));
  await waitForAssistantPuts();
  server.resetHandlers();
  if (typeof localStorage !== "undefined") localStorage.clear();
  resetMockDb();
});
afterAll(() => server.close());
