/**
 * Shared pointer singleton — one source of truth for the cursor position.
 *
 * Written by PointerFX.tsx (the global pointer-interaction layer) and read by
 * canvas effects like GeometricNetwork that want to react to the cursor without
 * wiring up their own listeners. Coordinates are in viewport (clientX/Y) space;
 * consumers map them into their own local space.
 *
 * `active` is false on touch / reduced-motion (PointerFX never starts there) and
 * whenever the pointer leaves the window — consumers should treat that as "no
 * cursor" and skip their reactive branch.
 */
export const pointer = { x: -9999, y: -9999, active: false };
