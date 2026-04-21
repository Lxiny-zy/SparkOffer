import { useState, useEffect, useId, useSyncExternalStore } from "react";
import { cn } from "@/lib/utils";

// Reactive dark-mode detection via MutationObserver on <html> class
const darkListeners = new Set<() => void>();
let darkSnapshot = typeof document !== "undefined" && document.documentElement.classList.contains("dark");

if (typeof document !== "undefined") {
  const observer = new MutationObserver(() => {
    const next = document.documentElement.classList.contains("dark");
    if (next !== darkSnapshot) {
      darkSnapshot = next;
      darkListeners.forEach((fn) => fn());
    }
  });
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
}

function subscribeDark(cb: () => void) { darkListeners.add(cb); return () => { darkListeners.delete(cb); }; }
function getDark() { return darkSnapshot; }

interface CatAvatarProps {
  size?: number;
  className?: string;
  mood?: "idle" | "curious" | "happy" | "thinking" | "sleepy" | "static";
}

/**
 * Premium cat avatar with 6 mood states, gradient fur, body/tail, and animations.
 * Moods: idle | curious | happy | thinking | sleepy | static
 */
export default function CatAvatar({ size = 48, className = "", mood = "idle" }: CatAvatarProps) {
  const uid = useId().replace(/:/g, "");
  const isDark = useSyncExternalStore(subscribeDark, getDark, () => false);
  const prefersReducedMotion = typeof window !== "undefined"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const useFancyFills = size > 24;
  const useFilters = size > 32;

  // Theme colors
  const bodyColor = isDark ? "#9373D1" : "#F5A623";
  const bodyColorDarker = isDark ? "#5B4A8A" : "#D4891A";
  const bodyColorLight = isDark ? "#C9B8E8" : "#FFEAA7";
  const eyeColor = isDark ? "#E8DEFF" : "#2D3436";
  const pupilColor = isDark ? "#381E72" : "#2D3436";
  const noseColor = isDark ? "#EFB8C8" : "#E17055";
  const cheekColorCenter = isDark ? "rgba(208,188,255,0.4)" : "rgba(255,118,117,0.4)";
  const cheekColorEdge = isDark ? "rgba(208,188,255,0)" : "rgba(255,118,117,0)";
  const innerEarColor = isDark ? "#EFB8C8" : "#FF7675";
  const whiskerColor = isDark ? "#A89DB8" : "#636E72";

  // Bounce-in on first render for happy mood
  const [justMounted, setJustMounted] = useState(true);
  useEffect(() => {
    if (justMounted) {
      const timer = setTimeout(() => setJustMounted(false), 600);
      return () => clearTimeout(timer);
    }
  }, [justMounted]);

  const canAnimate = !prefersReducedMotion && mood !== "static";

  // --- Eye rendering per mood ---
  function renderEyes() {
    // Happy / static: squint arcs ^_^
    if (mood === "happy" || mood === "static") {
      return (
        <>
          <path d="M20 28C20 28 23 25 26 28" stroke={eyeColor} strokeWidth="2.5" strokeLinecap="round" fill="none" />
          <path d="M38 28C38 28 41 25 44 28" stroke={eyeColor} strokeWidth="2.5" strokeLinecap="round" fill="none" />
        </>
      );
    }

    // Thinking: round eyes with pupils shifted left
    if (mood === "thinking") {
      return (
        <>
          <ellipse cx="23" cy="28" rx="4.5" ry="4.5" fill={eyeColor} />
          <ellipse cx="41" cy="28" rx="4.5" ry="4.5" fill={eyeColor} />
          <ellipse cx="21" cy="28" rx="2.5" ry="3" fill={pupilColor} />
          <ellipse cx="39" cy="28" rx="2.5" ry="3" fill={pupilColor} />
          <circle cx="22" cy="26.5" r="1.2" fill="white" />
          <circle cx="40" cy="26.5" r="1.2" fill="white" />
          {/* Thinking eyebrow */}
          <path d="M37 22Q41 20 45 22" stroke={eyeColor} strokeWidth="1.5" strokeLinecap="round" fill="none" />
        </>
      );
    }

    // Sleepy: very narrow eyes with heavy lids
    if (mood === "sleepy") {
      return (
        <>
          <ellipse cx="23" cy="28" rx="4" ry="1.5" fill={eyeColor} />
          <ellipse cx="41" cy="28" rx="4" ry="1.5" fill={eyeColor} />
          <ellipse cx="23" cy="28" rx="2" ry="1" fill={pupilColor} />
          <ellipse cx="41" cy="28" rx="2" ry="1" fill={pupilColor} />
          {/* Heavy eyelids */}
          <path d="M18 26C18 26 23 24 28 26" stroke={bodyColor} strokeWidth="3.5" strokeLinecap="round" fill="none" />
          <path d="M36 26C36 26 41 24 46 26" stroke={bodyColor} strokeWidth="3.5" strokeLinecap="round" fill="none" />
          {canAnimate && (
            <>
              <ellipse cx="23" cy="28" rx="4" ry="1.5" fill={eyeColor}>
                <animate attributeName="ry" values="1.5;1.5;0.3;1.5;1.5" keyTimes="0;0.45;0.5;0.55;1" dur="6s" repeatCount="indefinite" />
              </ellipse>
            </>
          )}
        </>
      );
    }

    // Curious: extra large sparkly eyes
    if (mood === "curious") {
      return (
        <>
          {useFilters && isDark && <ellipse cx="23" cy="28" rx="6" ry="6" fill={eyeColor} opacity="0.15" filter={`url(#glow-${uid})`} />}
          {useFilters && isDark && <ellipse cx="41" cy="28" rx="6" ry="6" fill={eyeColor} opacity="0.15" filter={`url(#glow-${uid})`} />}
          <ellipse cx="23" cy="28" rx="5.5" ry="5.5" fill={eyeColor} />
          <ellipse cx="41" cy="28" rx="5.5" ry="5.5" fill={eyeColor} />
          <ellipse cx="23" cy="28" rx="3.5" ry="4" fill={pupilColor} />
          <ellipse cx="41" cy="28" rx="3.5" ry="4" fill={pupilColor} />
          {/* Double sparkle highlights */}
          <circle cx="25.5" cy="26" r="2" fill="white" />
          <circle cx="43.5" cy="26" r="2" fill="white" />
          <circle cx="21.5" cy="30" r="1" fill="white" opacity="0.7" />
          <circle cx="39.5" cy="30" r="1" fill="white" opacity="0.7" />
        </>
      );
    }

    // Idle: theme-dependent eyes with blink
    if (isDark) {
      // Sleepy-ish half-open eyes
      return (
        <>
          <ellipse cx="23" cy="28" rx="4" ry="3" fill={eyeColor} />
          <ellipse cx="41" cy="28" rx="4" ry="3" fill={eyeColor} />
          <ellipse cx="23" cy="28" rx="2.5" ry="2.5" fill={pupilColor} />
          <ellipse cx="41" cy="28" rx="2.5" ry="2.5" fill={pupilColor} />
          <path d="M18 26C18 26 23 24 28 26" stroke={bodyColor} strokeWidth="3" strokeLinecap="round" fill="none" />
          <path d="M36 26C36 26 41 24 46 26" stroke={bodyColor} strokeWidth="3" strokeLinecap="round" fill="none" />
          <circle cx="24" cy="27" r="1" fill="white" opacity="0.8" />
          <circle cx="42" cy="27" r="1" fill="white" opacity="0.8" />
          {canAnimate && (
            <>
              {/* Blink overlay — body-colored ellipses briefly cover eyes */}
              <ellipse cx="23" cy="28" rx="4.5" ry="0" fill={bodyColor}>
                <animate attributeName="ry" values="0;0;4;0;0" keyTimes="0;0.46;0.5;0.54;1" dur="5s" repeatCount="indefinite" />
              </ellipse>
              <ellipse cx="41" cy="28" rx="4.5" ry="0" fill={bodyColor}>
                <animate attributeName="ry" values="0;0;4;0;0" keyTimes="0;0.46;0.5;0.54;1" dur="5s" repeatCount="indefinite" />
              </ellipse>
            </>
          )}
        </>
      );
    }

    // Idle light mode: big sparkly eyes with blink
    return (
      <>
        <ellipse cx="23" cy="28" rx="5" ry="5" fill={eyeColor} />
        <ellipse cx="41" cy="28" rx="5" ry="5" fill={eyeColor} />
        <ellipse cx="23" cy="28" rx="3" ry="3.5" fill={pupilColor} />
        <ellipse cx="41" cy="28" rx="3" ry="3.5" fill={pupilColor} />
        <circle cx="25" cy="26" r="1.5" fill="white" />
        <circle cx="43" cy="26" r="1.5" fill="white" />
        <circle cx="22" cy="30" r="0.8" fill="white" opacity="0.6" />
        <circle cx="40" cy="30" r="0.8" fill="white" opacity="0.6" />
        {canAnimate && (
          <>
            {/* Blink overlay — briefly covers eyes */}
            <ellipse cx="23" cy="28" rx="5.5" ry="0" fill={bodyColor}>
              <animate attributeName="ry" values="0;0;5.5;0;0" keyTimes="0;0.46;0.5;0.54;1" dur="4s" repeatCount="indefinite" />
            </ellipse>
            <ellipse cx="41" cy="28" rx="5.5" ry="0" fill={bodyColor}>
              <animate attributeName="ry" values="0;0;5.5;0;0" keyTimes="0;0.46;0.5;0.54;1" dur="4s" repeatCount="indefinite" />
            </ellipse>
          </>
        )}
      </>
    );
  }

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={cn(
        "transition-all duration-500",
        `cat-mood-${mood}`,
        mood === "happy" && justMounted && "animate-bounce-in",
        className
      )}
    >
      {/* Defs: gradients + filters */}
      <defs>
        {useFancyFills && (
          <>
            <linearGradient id={`fur-${uid}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={bodyColor} />
              <stop offset="100%" stopColor={bodyColorDarker} />
            </linearGradient>
            <radialGradient id={`belly-${uid}`} cx="50%" cy="60%" r="50%">
              <stop offset="0%" stopColor={bodyColorLight} stopOpacity="0.6" />
              <stop offset="100%" stopColor={bodyColorLight} stopOpacity="0" />
            </radialGradient>
            <linearGradient id={`ear-${uid}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={innerEarColor} stopOpacity="0.8" />
              <stop offset="100%" stopColor={innerEarColor} stopOpacity="0.4" />
            </linearGradient>
            <radialGradient id={`blush-${uid}`} cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor={cheekColorCenter} />
              <stop offset="100%" stopColor={cheekColorEdge} />
            </radialGradient>
          </>
        )}
        {useFilters && (
          <>
            <filter id={`shadow-${uid}`} x="-10%" y="-10%" width="120%" height="130%">
              <feGaussianBlur in="SourceAlpha" stdDeviation="1.5" />
              <feOffset dy="1" />
              <feComponentTransfer><feFuncA type="linear" slope="0.12" /></feComponentTransfer>
              <feMerge>
                <feMergeNode />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
            {isDark && (
              <filter id={`glow-${uid}`} x="-50%" y="-50%" width="200%" height="200%">
                <feGaussianBlur stdDeviation="2" />
              </filter>
            )}
          </>
        )}
      </defs>

      {/* Layer 1: Body + Tail */}
      <g className="cat-body" style={{ transformOrigin: "32px 52px" }}>
        {/* Tail */}
        <g className="cat-tail" style={{ transformOrigin: "44px 52px" }}>
          <path
            d="M44 52Q54 48,56 38Q58 34,54 32"
            stroke={useFancyFills ? `url(#fur-${uid})` : bodyColor}
            strokeWidth="4"
            strokeLinecap="round"
            fill="none"
          />
        </g>
        {/* Body */}
        <ellipse
          cx="32" cy="52" rx="14" ry="9"
          fill={useFancyFills ? `url(#fur-${uid})` : bodyColor}
          filter={useFilters ? `url(#shadow-${uid})` : undefined}
        />
        {/* Belly highlight */}
        <ellipse
          cx="32" cy="54" rx="9" ry="5"
          fill={useFancyFills ? `url(#belly-${uid})` : bodyColorLight}
          opacity={useFancyFills ? undefined : "0.5"}
        />
      </g>

      {/* Layer 2: Head */}
      <g className="cat-head" style={{ transformOrigin: "32px 30px" }}>
        <ellipse
          cx="32" cy="30" rx="22" ry="18"
          fill={useFancyFills ? `url(#fur-${uid})` : bodyColor}
          filter={useFilters ? `url(#shadow-${uid})` : undefined}
        />
        {/* Face lighter area */}
        <ellipse
          cx="32" cy="34" rx="14" ry="10"
          fill={useFancyFills ? `url(#belly-${uid})` : bodyColorLight}
          opacity={useFancyFills ? undefined : "0.5"}
        />
      </g>

      {/* Layer 3: Ears — rounded cat ears, not pointy */}
      <g className="cat-ears" style={{ transformOrigin: "32px 20px" }}>
        <g className="cat-ear-left" style={{ transformOrigin: "16px 18px" }}>
          <path d="M12 24Q6 14 14 8Q20 12 26 18Z" fill={useFancyFills ? `url(#fur-${uid})` : bodyColor} />
          <path d="M14 22Q9 15 15 10Q19 13 24 18Z" fill={useFancyFills ? `url(#ear-${uid})` : innerEarColor} opacity="0.6" />
        </g>
        <g className="cat-ear-right" style={{ transformOrigin: "48px 18px" }}>
          <path d="M52 24Q58 14 50 8Q44 12 38 18Z" fill={useFancyFills ? `url(#fur-${uid})` : bodyColor} />
          <path d="M50 22Q55 15 49 10Q45 13 40 18Z" fill={useFancyFills ? `url(#ear-${uid})` : innerEarColor} opacity="0.6" />
        </g>
      </g>

      {/* Layer 4: Face features */}
      <g className="cat-face">
        {/* Eyes */}
        {renderEyes()}

        {/* Cheek blush */}
        <circle cx="13" cy="34" r={useFancyFills ? "6" : "4"} fill={useFancyFills ? `url(#blush-${uid})` : cheekColorCenter} />
        <circle cx="51" cy="34" r={useFancyFills ? "6" : "4"} fill={useFancyFills ? `url(#blush-${uid})` : cheekColorCenter} />

        {/* Nose */}
        <path d="M30 36L32 38.5L34 36Z" fill={noseColor} />

        {/* Mouth */}
        <path d="M28 40C28 40 30 42 32 40" stroke={noseColor} strokeWidth="1.2" strokeLinecap="round" fill="none" />
        <path d="M32 40C32 40 34 42 36 40" stroke={noseColor} strokeWidth="1.2" strokeLinecap="round" fill="none" />

        {/* Whiskers */}
        <g opacity="0.5">
          <line x1="4" y1="32" x2="16" y2="34" stroke={whiskerColor} strokeWidth="0.8" />
          <line x1="4" y1="38" x2="16" y2="36" stroke={whiskerColor} strokeWidth="0.8" />
          <line x1="48" y1="34" x2="60" y2="32" stroke={whiskerColor} strokeWidth="0.8" />
          <line x1="48" y1="36" x2="60" y2="38" stroke={whiskerColor} strokeWidth="0.8" />
        </g>
      </g>
    </svg>
  );
}
