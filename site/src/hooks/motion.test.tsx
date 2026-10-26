import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Reveal } from "../components/Reveal";

let triggerIntersection: ((target: Element) => void) | undefined;

class TestIntersectionObserver implements IntersectionObserver {
  readonly root = null;
  readonly rootMargin = "0px";
  readonly thresholds: number[] = [];

  constructor(private readonly callback: IntersectionObserverCallback) {
    triggerIntersection = (target) => {
      const bounds = new DOMRect(0, 5000, 100, 100);
      const entry: IntersectionObserverEntry = {
        boundingClientRect: bounds,
        intersectionRect: bounds,
        isIntersecting: true,
        intersectionRatio: 1,
        rootBounds: null,
        target,
        time: 0,
      };
      this.callback([entry], this);
    };
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

function stubMatchMedia(matches: boolean) {
  vi.spyOn(window, "matchMedia").mockImplementation(
    (media) =>
      ({
        matches,
        media,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList,
  );
}

function placeElementBelowFold() {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 5000, 100, 100));
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  triggerIntersection = undefined;
});

describe("Reveal motion preference", () => {
  it("keeps content visible without reveal classes when reduced motion is enabled", () => {
    stubMatchMedia(true);
    placeElementBelowFold();
    vi.stubGlobal("IntersectionObserver", TestIntersectionObserver);

    render(
      <Reveal>
        <span>Reduced-motion content</span>
      </Reveal>,
    );

    const content = screen.getByText("Reduced-motion content");
    expect(content).toBeVisible();
    expect(content.parentElement).not.toHaveClass("reveal-pending");
  });

  it("reveals below-the-fold content when the observer reports intersection", () => {
    stubMatchMedia(false);
    placeElementBelowFold();
    vi.stubGlobal("IntersectionObserver", TestIntersectionObserver);

    render(
      <Reveal>
        <span>Observer content</span>
      </Reveal>,
    );

    const content = screen.getByText("Observer content");
    const element = content.parentElement!;
    expect(element).toHaveClass("reveal-pending");
    expect(content).toBeVisible();
    expect(triggerIntersection).toBeDefined();

    act(() => triggerIntersection?.(element));

    expect(element).toHaveClass("reveal-in");
    expect(element).not.toHaveClass("reveal-pending");
  });
});
