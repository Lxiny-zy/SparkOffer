import type { CSSProperties, ReactNode } from "react";
import { useInView } from "@/hooks/useInView";
import { cn } from "@/lib/utils";

/**
 * Generic scroll-reveal wrapper. Children start hidden+offset and play a
 * one-shot enter animation the first time they scroll into view.
 *
 * - `variant` picks the keyframe set (all defined as --animate-* tokens).
 * - `delay` (ms) staggers siblings without nth-child CSS, so it works with
 *   dynamic lists; capped naturally by usage, keep to ~0-400ms.
 * - Reduced-motion users see content instantly (global media query zeroes
 *   animations, and we never gate visibility on JS when IO is missing).
 */
export default function Reveal({
  children,
  variant = "fade-in-up",
  delay = 0,
  className,
  as: Tag = "div",
  threshold,
}: {
  children: ReactNode;
  variant?: "fade-in" | "fade-in-up" | "slide-up" | "scale-in";
  delay?: number;
  className?: string;
  as?: "div" | "section" | "article" | "li" | "span";
  threshold?: number;
}) {
  const [ref, inView] = useInView<HTMLDivElement>(threshold);

  const style: CSSProperties = { animationDelay: delay ? `${delay}ms` : undefined };
  const Component = Tag as "div";

  return (
    <Component
      ref={ref}
      style={style}
      className={cn(
        inView ? `animate-${variant}` : "opacity-0",
        className,
      )}
    >
      {children}
    </Component>
  );
}
