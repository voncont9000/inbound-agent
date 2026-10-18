import { setupWorker } from "msw/browser";
import { delay, http } from "msw";
import { handlers } from "./handlers";

export const worker = setupWorker(...handlers);

export async function startMockWorker(): Promise<void> {
  worker.use(
    http.all("*/api/*", async () => {
      await delay(150);
      return undefined;
    }),
  );
  await worker.start({ onUnhandledRequest: "bypass" });
}
