// motion.js — the blotter's entire animation budget: a rAF count-up, a
// typewriter, and FLIP measurement helpers. prefers-reduced-motion collapses
// every one of them to an instant state change.

export function reducedMotion() {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}

export function tokenNumber(name, fallback) {
  if (typeof window === 'undefined') return fallback;
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name);
  const n = parseFloat(raw);
  return Number.isFinite(n) ? n : fallback;
}

// Animate a number from -> to, calling onFrame(value) each frame.
export function countUp(from, to, onFrame, onDone) {
  if (reducedMotion() || from === to) {
    onFrame(to);
    onDone?.();
    return () => {};
  }
  const duration = tokenNumber('--count-ms', 700);
  const start = performance.now();
  let raf;
  const tick = (now) => {
    const t = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - t, 3);
    onFrame(Math.round(from + (to - from) * eased));
    if (t < 1) raf = requestAnimationFrame(tick);
    else onDone?.();
  };
  raf = requestAnimationFrame(tick);
  return () => cancelAnimationFrame(raf);
}

// Typewriter: reveal text character by character, calling onFrame(partial).
export function typeOn(text, onFrame, onDone) {
  if (reducedMotion()) {
    onFrame(text);
    onDone?.();
    return () => {};
  }
  const perChar = tokenNumber('--type-ms', 16);
  let i = 0;
  const interval = setInterval(() => {
    i += 1;
    onFrame(text.slice(0, i));
    if (i >= text.length) {
      clearInterval(interval);
      onDone?.();
    }
  }, perChar);
  return () => clearInterval(interval);
}

// FLIP: given a map of key -> previous DOMRect and current elements,
// invert to old positions then transition to identity. Runs once per re-sort.
export function runFlip(prevRects, currentEls) {
  if (reducedMotion()) return;
  const moves = [];
  for (const [key, el] of currentEls) {
    const prev = prevRects.get(key);
    if (!el || !prev) continue;
    const next = el.getBoundingClientRect();
    const dx = prev.left - next.left;
    const dy = prev.top - next.top;
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;
    moves.push([el, dx, dy]);
  }
  if (!moves.length) return;
  for (const [el, dx, dy] of moves) {
    el.style.transition = 'none';
    el.style.transform = `translate(${dx}px, ${dy}px)`;
  }
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      for (const [el] of moves) {
        el.style.transition = 'transform var(--t-flip)';
        el.style.transform = '';
      }
    });
  });
}
