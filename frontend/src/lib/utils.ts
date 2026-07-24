import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs) {
  return twMerge(clsx(inputs));
}

/** Scroll an element within its nearest scrollable container without moving
 * unrelated ancestors (notably the fixed app shell and sidebars). */
export function scrollToElement(element: HTMLElement | null, behavior: ScrollBehavior = "smooth") {
  if (!element || typeof window === "undefined") return;

  let parent = element.parentElement;
  while (parent) {
    const style = window.getComputedStyle(parent);
    const scrollable = /(auto|scroll)/.test(style.overflowY) && parent.scrollHeight > parent.clientHeight;
    if (scrollable) {
      const parentRect = parent.getBoundingClientRect();
      const elementRect = element.getBoundingClientRect();
      const nextTop = parent.scrollTop + elementRect.top - parentRect.top - 24;
      parent.scrollTo({ top: Math.max(0, nextTop), behavior });
      return;
    }
    parent = parent.parentElement;
  }

  const top = window.scrollY + element.getBoundingClientRect().top - 24;
  window.scrollTo({ top: Math.max(0, top), behavior });
}
