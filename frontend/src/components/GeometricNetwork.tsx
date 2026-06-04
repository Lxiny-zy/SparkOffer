import { useEffect, useRef } from "react";
import { pointer } from "@/lib/pointer";

interface Node {
  x: number;
  y: number;
  vx: number;
  vy: number;
}

const MAX_NODES = 32;
const LINK_DIST = 160;
const NODE_RADIUS = 1.8;
const SPEED = 0.22;
const DENSITY = 38000; // px² per node
const CURSOR_DIST = 200; // nodes within this of the cursor link up to it

export default function GeometricNetwork() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let nodes: Node[] = [];
    let raf = 0;
    let colors = readColors();
    // Cached canvas viewport rect — maps the shared pointer (clientX/Y) into
    // canvas-local space. Refreshed on resize + ResizeObserver (sidebar collapse).
    let rect = canvas.getBoundingClientRect();

    function readColors() {
      const root = getComputedStyle(document.documentElement);
      const isDark = document.documentElement.classList.contains("dark");
      const line = toRgb(root.getPropertyValue("--aurora-2").trim()) || (isDark ? [208, 188, 255] : [103, 80, 164]);
      const node = toRgb(root.getPropertyValue("--aurora-1").trim()) || (isDark ? [167, 139, 250] : [103, 80, 164]);
      return {
        line,
        node,
        lineOpacity: isDark ? 0.32 : 0.22,
        nodeOpacity: isDark ? 0.78 : 0.55,
      };
    }

    function resize() {
      const dpr = window.devicePixelRatio || 1;
      const w = canvas!.clientWidth;
      const h = canvas!.clientHeight;
      canvas!.width = Math.max(1, Math.floor(w * dpr));
      canvas!.height = Math.max(1, Math.floor(h * dpr));
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);

      const target = Math.min(MAX_NODES, Math.max(10, Math.round((w * h) / DENSITY)));
      if (nodes.length === 0) {
        nodes = Array.from({ length: target }, () => ({
          x: Math.random() * w,
          y: Math.random() * h,
          vx: (Math.random() - 0.5) * SPEED,
          vy: (Math.random() - 0.5) * SPEED,
        }));
      } else if (nodes.length < target) {
        while (nodes.length < target) {
          nodes.push({
            x: Math.random() * w,
            y: Math.random() * h,
            vx: (Math.random() - 0.5) * SPEED,
            vy: (Math.random() - 0.5) * SPEED,
          });
        }
      } else if (nodes.length > target) {
        nodes.length = target;
      }
      rect = canvas!.getBoundingClientRect();
    }

    function draw() {
      const w = canvas!.clientWidth;
      const h = canvas!.clientHeight;
      ctx!.clearRect(0, 0, w, h);

      if (!reduced) {
        for (const n of nodes) {
          n.x += n.vx;
          n.y += n.vy;
          if (n.x < 0) { n.x = 0; n.vx *= -1; }
          else if (n.x > w) { n.x = w; n.vx *= -1; }
          if (n.y < 0) { n.y = 0; n.vy *= -1; }
          else if (n.y > h) { n.y = h; n.vy *= -1; }
        }
      }

      // Links: opacity falls off with distance
      ctx!.lineWidth = 1;
      const [lr, lg, lb] = colors.line;
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const dx = nodes[i].x - nodes[j].x;
          const dy = nodes[i].y - nodes[j].y;
          const d2 = dx * dx + dy * dy;
          if (d2 < LINK_DIST * LINK_DIST) {
            const d = Math.sqrt(d2);
            const a = (1 - d / LINK_DIST) * colors.lineOpacity;
            ctx!.strokeStyle = `rgba(${lr},${lg},${lb},${a})`;
            ctx!.beginPath();
            ctx!.moveTo(nodes[i].x, nodes[i].y);
            ctx!.lineTo(nodes[j].x, nodes[j].y);
            ctx!.stroke();
          }
        }
      }

      // Cursor reactivity — links reach toward the pointer when it's over the
      // canvas. Brighter than ambient links so the network visibly "leans in".
      // Purely visual (no velocity changes) → stable, no clumping.
      if (!reduced && pointer.active) {
        const cx = pointer.x - rect.left;
        const cy = pointer.y - rect.top;
        if (cx >= 0 && cx <= w && cy >= 0 && cy <= h) {
          for (const n of nodes) {
            const dx = n.x - cx;
            const dy = n.y - cy;
            const d2 = dx * dx + dy * dy;
            if (d2 < CURSOR_DIST * CURSOR_DIST) {
              const d = Math.sqrt(d2);
              const t = 1 - d / CURSOR_DIST;
              ctx!.strokeStyle = `rgba(${lr},${lg},${lb},${t * colors.lineOpacity * 2})`;
              ctx!.beginPath();
              ctx!.moveTo(cx, cy);
              ctx!.lineTo(n.x, n.y);
              ctx!.stroke();
            }
          }
        }
      }

      // Nodes
      const [nr, ng, nb] = colors.node;
      ctx!.fillStyle = `rgba(${nr},${ng},${nb},${colors.nodeOpacity})`;
      for (const n of nodes) {
        ctx!.beginPath();
        ctx!.arc(n.x, n.y, NODE_RADIUS, 0, Math.PI * 2);
        ctx!.fill();
      }

      raf = requestAnimationFrame(draw);
    }

    resize();
    draw();

    // Debounce resize: dragging a window edge fires hundreds of events; we only
    // need to re-measure once the user stops, otherwise we keep rebuilding the
    // node array and dropping frames.
    let resizeTimer: number | null = null;
    const onResize = () => {
      if (resizeTimer !== null) window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(() => {
        resize();
        resizeTimer = null;
      }, 150);
    };
    window.addEventListener("resize", onResize);

    const observer = new MutationObserver(() => { colors = readColors(); });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });

    // Keep the cached rect fresh when the canvas changes size without a window
    // resize event (e.g. the sidebar collapsing widens the main area).
    const ro = new ResizeObserver(() => { rect = canvas!.getBoundingClientRect(); });
    ro.observe(canvas);

    return () => {
      cancelAnimationFrame(raf);
      if (resizeTimer !== null) window.clearTimeout(resizeTimer);
      window.removeEventListener("resize", onResize);
      observer.disconnect();
      ro.disconnect();
    };
  }, []);

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
