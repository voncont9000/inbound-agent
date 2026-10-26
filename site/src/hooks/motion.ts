import { useEffect, useRef, useState, type RefObject } from "react";

export const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

export function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia(REDUCED_MOTION_QUERY).matches
    : false;
}

/** False during SSR and the hydration render, then tracks the media query. */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia(REDUCED_MOTION_QUERY);
    const update = () => setReduced(query.matches);
    update();
    query.addEventListener?.("change", update);
    return () => query.removeEventListener?.("change", update);
  }, []);
  return reduced;
}

type RevealOptions = { delay?: number; threshold?: number };

/**
 * Fades an element up once it scrolls into view. The hidden class is added only after hydration,
 * so prerendered HTML is always visible, and never under reduced motion.
 */
export function useReveal<T extends HTMLElement>({ delay = 0, threshold = 0.15 }: RevealOptions = {}): RefObject<T | null> {
  const ref = useRef<T>(null);
  useEffect(() => {
    const element = ref.current;
    if (!element || prefersReducedMotion() || typeof IntersectionObserver === "undefined") return;
    const rect = element.getBoundingClientRect();
    if (rect.top < window.innerHeight * 0.9 && rect.bottom > 0 && delay === 0) return;
    element.style.setProperty("--reveal-delay", `${delay}ms`);
    element.classList.add("reveal-pending");
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          element.classList.add("reveal-in");
          element.classList.remove("reveal-pending");
          observer.disconnect();
        }
      },
      { threshold, rootMargin: "0px 0px -8% 0px" },
    );
    observer.observe(element);
    return () => {
      observer.disconnect();
      element.classList.remove("reveal-pending");
    };
  }, [delay, threshold]);
  return ref;
}

/** True while the element is on screen and the tab is visible. */
export function useInView<T extends HTMLElement>(threshold = 0.25): [RefObject<T | null>, boolean] {
  const ref = useRef<T>(null);
  const [inView, setInView] = useState(false);
  useEffect(() => {
    const element = ref.current;
    if (!element || typeof IntersectionObserver === "undefined") return;
    let onScreen = false;
    const update = () => setInView(onScreen && document.visibilityState !== "hidden");
    const observer = new IntersectionObserver(
      ([entry]) => {
        onScreen = entry.isIntersecting;
        update();
      },
      { threshold },
    );
    observer.observe(element);
    document.addEventListener("visibilitychange", update);
    return () => {
      observer.disconnect();
      document.removeEventListener("visibilitychange", update);
    };
  }, [threshold]);
  return [ref, inView];
}
