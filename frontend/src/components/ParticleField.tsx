import { useEffect, useRef } from "react";
import { pointer } from "@/lib/pointer";

interface Mote {
  nx: number;
  ny: number;
  depth: number;
  rise: number;
  phase: number;
  freq: number;
  r: number;
}

/**
 * 漂浮微尘粒子场 —— 慢速上浮 + 指针视差 + 呼吸闪烁。
 * 纯氛围层：不拦截指针事件，仅借共享 pointer 单例做整体视差。
 * 主题切换时重读 --sig-accent；尊重 prefers-reduced-motion（静止一帧）。
 */
export default function ParticleField({
  density = 26000,
  max = 90,
  parallax = 14,
}: {
  density?: number;
  max?: number;
  parallax?: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const motes: Mote[] = [];
    let raf = 0;
    let last = 0;
    let inViewport = true;
    let pageVisible = !document.hidden;
    let accent = readAccent();
    let px = 0;
    let py = 0;

    function readAccent(): [number, number, number] {
      const scope = canvas!.closest(".sig-root") || document.documentElement;
      const raw = getComputedStyle(scope).getPropertyValue("--sig-accent").trim();
      const rgb = toRgb(raw);
      if (rgb) return rgb;
      return document.documentElement.classList.contains("dark")
        ? [139, 108, 255]
        : [109, 59, 255];
    }

    function resize() {
      const dpr = window.devicePixelRatio || 1;
      const w = canvas!.clientWidth;
      const h = canvas!.clientHeight;
      canvas!.width = Math.max(1, Math.floor(w * dpr));
      canvas!.height = Math.max(1, Math.floor(h * dpr));
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
      const target = Math.min(max, Math.max(18, Math.round((w * h) / density)));
      while (motes.length < target) motes.push(spawn(true));
      if (motes.length > target) motes.length = target;
    }

    function spawn(anywhere: boolean): Mote {
      const depth = 0.25 + Math.random() * 0.75;
      return {
        nx: Math.random(),
        ny: anywhere ? Math.random() : 1 + Math.random() * 0.12,
        depth,
        rise: (22 + Math.random() * 38) * depth,
        phase: Math.random() * Math.PI * 2,
        freq: 0.5 + Math.random() * 1.2,
        r: 0.9 + depth * 1.9,
      };
    }

    function stop() {
      if (raf !== 0) {
        cancelAnimationFrame(raf);
        raf = 0;
      }
    }

    function schedule() {
      if (reduced || !inViewport || !pageVisible || raf !== 0) return;
      raf = requestAnimationFrame(draw);
    }

    function draw(now: number) {
      raf = 0;
      const dt = Math.min(50, now - (last || now));
      last = now;
      const w = canvas!.clientWidth;
      const h = canvas!.clientHeight;
      ctx!.clearRect(0, 0, w, h);

      let tx = 0;
      let ty = 0;
      if (!reduced && pointer.active) {
        tx = (pointer.x / window.innerWidth - 0.5) * parallax;
        ty = (pointer.y / window.innerHeight - 0.5) * parallax;
      }
      px += (tx - px) * 0.045;
      py += (ty - py) * 0.045;

      const [cr, cg, cb] = accent;
      for (const m of motes) {
        if (!reduced) {
          m.ny -= (m.rise * dt) / 1000 / h;
          m.phase += (m.freq * dt) / 1000;
          if (m.ny < -0.05) Object.assign(m, spawn(false));
        }
        const tw = 0.5 + 0.5 * Math.sin(m.phase * Math.PI * 2);
        const a = (0.28 + 0.72 * tw) * (0.3 + m.depth * 0.7);
        const x = m.nx * w + px * m.depth;
        const y = m.ny * h + py * m.depth;
        ctx!.fillStyle = `rgba(${cr},${cg},${cb},${a})`;
        ctx!.beginPath();
        ctx!.arc(x, y, m.r, 0, Math.PI * 2);
        ctx!.fill();
      }
      schedule();
    }

    resize();
    if (reduced) draw(0);
    else raf = requestAnimationFrame(draw);

    let resizeTimer: number | null = null;
    const onResize = () => {
      if (resizeTimer !== null) window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(() => {
        resize();
        if (reduced) draw(0);
        resizeTimer = null;
      }, 150);
    };
    window.addEventListener("resize", onResize);

    const themeObserver = new MutationObserver(() => {
      accent = readAccent();
      if (reduced || !pageVisible || !inViewport) draw(0);
    });
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });

    const onVisibility = () => {
      pageVisible = !document.hidden;
      if (pageVisible) { resize(); if (reduced) draw(0); else schedule(); }
      else stop();
    };
    document.addEventListener("visibilitychange", onVisibility);

    const inViewObserver = new IntersectionObserver((entries) => {
      const next = entries[0]?.isIntersecting ?? true;
      if (next === inViewport) return;
      inViewport = next;
      if (inViewport) { resize(); if (reduced) draw(0); else schedule(); }
      else stop();
    }, { threshold: 0.01 });
    inViewObserver.observe(canvas);

    return () => {
      stop();
      if (resizeTimer !== null) window.clearTimeout(resizeTimer);
      window.removeEventListener("resize", onResize);
      document.removeEventListener("visibilitychange", onVisibility);
      themeObserver.disconnect();
      inViewObserver.disconnect();
    };
  }, [density, max, parallax]);

  return (
    <canvas
      ref={canvasRef}
      className="absolute inset-0 w-full h-full pointer-events-none"
      aria-hidden="true"
    />
  );
}

function toRgb(value: string): [number, number, number] | null {
  if (!value) return null;
  const v = value.trim();
  if (v.startsWith("#")) {
    const hex = v.length === 4
      ? v.slice(1).split("").map((c) => c + c).join("")
      : v.slice(1);
    if (hex.length !== 6) return null;
    const r = parseInt(hex.slice(0, 2), 16);
    const g = parseInt(hex.slice(2, 4), 16);
    const b = parseInt(hex.slice(4, 6), 16);
    if ([r, g, b].some(Number.isNaN)) return null;
    return [r, g, b];
  }
  const m = v.match(/rgba?\(([^)]+)\)/);
  if (m) {
    const parts = m[1].split(",").map((x) => parseFloat(x.trim()));
    if (parts.length >= 3 && parts.slice(0, 3).every((n) => !Number.isNaN(n))) {
      return [parts[0], parts[1], parts[2]];
    }
  }
  return null;
}