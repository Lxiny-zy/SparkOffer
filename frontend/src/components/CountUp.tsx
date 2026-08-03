import { useCountUp } from "@/hooks/useCountUp";
import { useInView } from "@/hooks/useInView";

/**
 * Stat number that counts up from 0 the first time it scrolls into view.
 * Renders a plain <span>; style it from the parent. Falls back to the final
 * value immediately for reduced-motion users.
 */
export default function CountUp({
  value,
  duration,
  decimals = 0,
  suffix,
  prefix,
}: {
  value: number;
  duration?: number;
  decimals?: number;
  suffix?: string;
  prefix?: string;
}) {
  const [ref, inView] = useInView<HTMLSpanElement>(0.4);
  const display = useCountUp(inView ? value : 0, { duration, decimals });

  return (
    <span ref={ref}>
      {prefix}
      {display}
      {suffix}
    </span>
  );
}
