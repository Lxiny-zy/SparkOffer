import { useCallback, useRef, type MouseEvent } from "react";

/**
 * Cursor-tracked tilt/parallax helper. Writes two normalized CSS vars on the
 * ref'd element as the pointer moves over it:
 *   --px, --py ∈ [-0.5, 0.5]  (0,0 = centered)
 *
 * It deliberately does NOT set `transform` itself — children read --px/--py and
 * apply their own transform, so a card and the glow behind it can parallax
 * independently. Reset to 0 on leave (let CSS transition the ease-out).
 *
 * No-ops on coarse pointers (touch) and `prefers-reduced-motion`.
 */
export function useTilt() {
  const ref = useRef<HTMLDivElement>(null);

  const onMouseMove = useCallback((e: MouseEvent) => {
    const el = ref.current;
    if (!el) return;
    if (
      window.matchMedia("(pointer: coarse)").matches ||
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    )
      return;
    const r = el.getBoundingClientRect();
    el.style.setProperty("--px", ((e.clientX - r.left) / r.width - 0.5).toFixed(3));
    el.style.setProperty("--py", ((e.clientY - r.top) / r.height - 0.5).toFixed(3));
  }, []);

  const onMouseLeave = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    el.style.setProperty("--px", "0");
    el.style.setProperty("--py", "0");
  }, []);

  return { ref, onMouseMove, onMouseLeave };
}
