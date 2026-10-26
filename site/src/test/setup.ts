import "@testing-library/jest-dom/vitest";

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (media: string): MediaQueryList => ({
    matches: false,
    media,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

class StubIntersectionObserver implements IntersectionObserver {
  readonly root = null;
  readonly rootMargin = "0px";
  readonly thresholds: number[] = [];

  constructor(callback: IntersectionObserverCallback) {
    void callback;
  }

  disconnect() {}
  observe(target: Element) {
    void target;
  }
  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }
  unobserve(target: Element) {
    void target;
  }
}

globalThis.IntersectionObserver = StubIntersectionObserver;
