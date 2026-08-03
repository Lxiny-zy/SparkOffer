import { useEffect, useRef, useState } from "react";

/**
 * One-shot in-view detection for scroll-reveal effects.
 *
 * Returns [ref, inView]: attach `ref` to the element; `inView` flips to true
 * the first time it intersects the viewport and stays true (observer
 * disconnects), so reveal animations never replay on scroll-back.
 *
 * Falls back to `true` when IntersectionObserver is unavailable (SSR / very
 * old browsers) so content is never stuck hidden. `prefers-reduced-motion`
 * handling is left to the CSS animation layer (global media query kills
 * animations), keeping this hook purely about visibility.
 */
export function useInView<T extends HTMLElement = HTMLDivElement>(
  threshold = 0.15,
  rootMargin = "0px 0px -8% 0px",
) {
  const ref = useRef<T | null>(null);
  const [inView, setInView] = useState(
    () => typeof window === "undefined" || !("IntersectionObserver" in window),
  );

  useEffect(() => {
    const el = ref.current;
    if (!el || !("IntersectionObserver" in window)) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setInView(true);
          observer.disconnect();
        }
      },
      { threshold, rootMargin },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [threshold, rootMargin]);

  return [ref, inView] as const;
}
