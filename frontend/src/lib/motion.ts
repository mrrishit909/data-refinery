export const EASE_OUT = "cubic-bezier(0.2, 0.7, 0.2, 1)";
export const EASE_IN = "cubic-bezier(0.4, 0, 1, 1)";

export function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Web Animations API helper. Elements are visible by default; the animation only runs in
 * browsers that support it and when the visitor has not asked for reduced motion, so content
 * never depends on JavaScript-driven motion to appear.
 */
export function animate(
  el: Element | null | undefined,
  keyframes: Keyframe[],
  options: KeyframeAnimationOptions,
): Animation | null {
  if (!el || prefersReducedMotion() || typeof (el as HTMLElement).animate !== "function") return null;
  return (el as HTMLElement).animate(keyframes, { fill: "backwards", easing: EASE_OUT, ...options });
}

/** Draw an SVG stroke from nothing to complete. */
export function drawStroke(path: SVGPathElement | null, duration: number, delay = 0): Animation | null {
  if (!path || typeof path.getTotalLength !== "function") return null;
  const length = path.getTotalLength();
  if (!Number.isFinite(length) || length === 0) return null;
  return animate(
    path,
    [
      { strokeDasharray: `${length}`, strokeDashoffset: `${length}` },
      { strokeDasharray: `${length}`, strokeDashoffset: "0" },
    ],
    { duration, delay },
  );
}
