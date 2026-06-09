import { useEffect, useRef } from "react";
import { pointer } from "@/lib/pointer";

/**
 * Global pointer-interaction layer. Mounted once at the app root; powers three
 * cursor effects across every route from a single rAF-throttled listener:
 *
 *  1. Ambient glow — a faint radial that softly trails the cursor (the rendered
 *     `.cursor-glow` div, driven by --cursor-x/--cursor-y on :root).
 *  2. Card spotlight — sets --mx/--my (in %) on the hovered `[data-spotlight]`
 *     element so its `.spotlight` highlight follows the cursor.
 *  3. Magnetic pull — nudges the hovered `[data-magnetic]` element toward the
 *     cursor (capped), resetting on leave.
 *
 * It also feeds the shared `pointer` singleton so canvas effects (GeometricNetwork)
 * can react without their own listeners.
 *
 * Skips entirely on coarse pointers (touch) and `prefers-reduced-motion` — the
 * glow div stays inert (opacity 0) and no listeners are attached.
 */

const MAGNET_STRENGTH = 0.3; // fraction of cursor-offset applied as translation
const MAGNET_MAX = 6; // px cap, kept small so it reads as a subtle pull

const clamp = (v: number, max: number) => Math.max(-max, Math.min(max, v));

export default function PointerFX() {
  const glowRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const fine = window.matchMedia("(pointer: fine)").matches;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!fine || reduced) return;

    const root = document.documentElement;
    const glow = glowRef.current;

    let rafId = 0;
    let queued = false;
    let x = 0;
    let y = 0;
    let spotlightEl: HTMLElement | null = null;
    let magnetEl: HTMLElement | null = null;

    const apply = () => {
      queued = false;

      root.style.setProperty("--cursor-x", `${x}px`);
      root.style.setProperty("--cursor-y", `${y}px`);
      pointer.x = x;
      pointer.y = y;
      pointer.active = true;
      if (glow) glow.classList.add("is-active");

      if (spotlightEl) {
        const r = spotlightEl.getBoundingClientRect();
        if (r.width && r.height) {
          spotlightEl.style.setProperty("--mx", `${((x - r.left) / r.width) * 100}%`);
          spotlightEl.style.setProperty("--my", `${((y - r.top) / r.height) * 100}%`);
        }
      }

      if (magnetEl) {
        const r = magnetEl.getBoundingClientRect();
        const dx = clamp((x - (r.left + r.width / 2)) * MAGNET_STRENGTH, MAGNET_MAX);
        const dy = clamp((y - (r.top + r.height / 2)) * MAGNET_STRENGTH, MAGNET_MAX);
        magnetEl.style.transform = `translate(${dx}px, ${dy}px)`;
      }
    };

    const onMove = (e: PointerEvent) => {
      x = e.clientX;
      y = e.clientY;

      const target = e.target as Element | null;
      spotlightEl = (target?.closest?.("[data-spotlight]") as HTMLElement) ?? null;

      const mg = (target?.closest?.("[data-magnetic]") as HTMLElement) ?? null;
      if (mg !== magnetEl) {
        if (magnetEl) magnetEl.style.transform = "";
        magnetEl = mg;
      }

      if (!queued) {
        queued = true;
        rafId = requestAnimationFrame(apply);
      }
    };

    const hide = () => {
      pointer.active = false;
      if (glow) glow.classList.remove("is-active");
      if (magnetEl) {
        magnetEl.style.transform = "";
        magnetEl = null;
      }
      spotlightEl = null;
    };

    window.addEventListener("pointermove", onMove, { passive: true });
    window.addEventListener("pointerdown", onMove, { passive: true });
    document.addEventListener("pointerleave", hide);
    window.addEventListener("blur", hide);

    return () => {
      cancelAnimationFrame(rafId);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerdown", onMove);
      document.removeEventListener("pointerleave", hide);
      window.removeEventListener("blur", hide);
      root.style.removeProperty("--cursor-x");
      root.style.removeProperty("--cursor-y");
      pointer.active = false;
    };
  }, []);

  return <div ref={glowRef} className="cursor-glow" aria-hidden="true" />;
}
