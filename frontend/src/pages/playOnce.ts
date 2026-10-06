import { useEffect, useState, type RefObject } from "react";

/** The time `ref` first came half into view or filled half the viewport; null until then and under
 *  reduced motion. `replay` starts it again. */
export function usePlayOnce(ref: RefObject<Element | null>) {
  const [still] = useState(() => matchMedia("(prefers-reduced-motion: reduce)").matches);
  const [start, setStart] = useState<number | null>(null);

  useEffect(() => {
    if (still || !ref.current) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        const half = (entry.rootBounds?.height ?? Infinity) / 2;
        if (entry.intersectionRatio < 0.5 && entry.intersectionRect.height < half) return;
        observer.disconnect();
        setStart(performance.now());
      },
      { threshold: [0, 0.1, 0.2, 0.3, 0.4, 0.5] },
    );
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, [still, ref]);

  return { still, start, replay: () => setStart(performance.now()) };
}
