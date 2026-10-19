import type { WebCallSessionFactory } from "../call/webCall";

/** Mock-mode stand-in for the browser call: connects after a short delay and plays no audio. */
export const createFakeWebCallSession: WebCallSessionFactory = (_url, events) => {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let ended = false;
  return {
    async start() {
      timer = setTimeout(() => events.onConnected(), 700);
    },
    setMuted() {},
    hangUp() {
      if (ended) return;
      ended = true;
      clearTimeout(timer);
      events.onEnded();
    },
  };
};
