import { useEffect, useRef, useState } from "react";

/**
 * Animated number that eases from its previous value to `target` whenever
 * `target` changes. Respects `prefers-reduced-motion` by snapping instantly.
 *
 * Returns the current display value (already rounded to `decimals`). Pair with
 * `useInView` so stats only count up once they scroll into view.
 */
export function useCountUp(
  target: number,
  { duration = 900, decimals = 0 }: { duration?: number; decimals?: number } = {},
) {
  const [value, setValue] = useState(target);
  // Track the last settled value in a ref so each new animation starts from it
  // without reading state inside the effect.
  const fromRef = useRef(target);

  useEffect(() => {
    const reduced =
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    let raf = 0;
    if (reduced || duration <= 0) {
      // Defer the snap into a rAF callback so setState never runs synchronously
      // in the effect body (React's set-state-in-effect guidance).
      raf = requestAnimationFrame(() => {
        fromRef.current = target;
        setValue(target);
      });
      return () => cancelAnimationFrame(raf);
    }

    const from = fromRef.current;
    let start = 0;
    const tick = (now: number) => {
      if (!start) start = now;
      const t = Math.min(1, (now - start) / duration);
      // ease-out cubic so the count decelerates into its final value.
      const eased = 1 - Math.pow(1 - t, 3);
      const next = from + (target - from) * eased;
      fromRef.current = next;
      setValue(next);
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration]);

  return Number(value.toFixed(decimals));
}
