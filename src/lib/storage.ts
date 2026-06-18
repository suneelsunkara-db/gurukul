// Tiny, SSR-safe localStorage wrapper. Docusaurus pre-renders pages on
// the server, where `window` is undefined, so every read has to be
// gated behind a hook that runs only after hydration. All widget state
// lives under the `gurukul:` namespace so it's easy to inspect and wipe.

import {useEffect, useState, useCallback} from 'react';

const NS = 'gurukul:';

function safeRead<T>(key: string, fallback: T): T {
  if (typeof window === 'undefined') return fallback;
  try {
    const raw = window.localStorage.getItem(NS + key);
    if (raw == null) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function safeWrite<T>(key: string, value: T): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(NS + key, JSON.stringify(value));
    // Broadcast so other mounted widgets re-render.
    window.dispatchEvent(
      new CustomEvent('gurukul:storage', {detail: {key}}),
    );
  } catch {
    // Quota or privacy mode — silently ignore. Nothing here is critical.
  }
}

/**
 * React hook for a single localStorage-backed value. Returns the
 * fallback during SSR and on the first client render to avoid hydration
 * mismatches, then swaps to the real persisted value on mount.
 */
export function usePersistentState<T>(
  key: string,
  fallback: T,
): [T, (next: T | ((prev: T) => T)) => void] {
  const [value, setValue] = useState<T>(fallback);

  useEffect(() => {
    setValue(safeRead<T>(key, fallback));
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as {key: string} | undefined;
      if (!detail || detail.key === key) {
        setValue(safeRead<T>(key, fallback));
      }
    };
    window.addEventListener('gurukul:storage', handler);
    return () => window.removeEventListener('gurukul:storage', handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const setter = useCallback(
    (next: T | ((prev: T) => T)) => {
      setValue((prev) => {
        const v =
          typeof next === 'function' ? (next as (p: T) => T)(prev) : next;
        safeWrite(key, v);
        return v;
      });
    },
    [key],
  );

  return [value, setter];
}

/**
 * Read every key under the `gurukul:` namespace. Used by the dashboard
 * to aggregate confidence, open-problems, research seeds, and critiques
 * across topics.
 */
export function readAll(): Record<string, unknown> {
  if (typeof window === 'undefined') return {};
  const out: Record<string, unknown> = {};
  for (let i = 0; i < window.localStorage.length; i++) {
    const fullKey = window.localStorage.key(i);
    if (!fullKey || !fullKey.startsWith(NS)) continue;
    const k = fullKey.slice(NS.length);
    try {
      out[k] = JSON.parse(window.localStorage.getItem(fullKey)!);
    } catch {
      out[k] = window.localStorage.getItem(fullKey);
    }
  }
  return out;
}

/** Clear every gurukul-namespaced key. Used by the "reset" button. */
export function clearAll(): void {
  if (typeof window === 'undefined') return;
  const toDelete: string[] = [];
  for (let i = 0; i < window.localStorage.length; i++) {
    const k = window.localStorage.key(i);
    if (k && k.startsWith(NS)) toDelete.push(k);
  }
  toDelete.forEach((k) => window.localStorage.removeItem(k));
  window.dispatchEvent(new CustomEvent('gurukul:storage', {detail: {}}));
}
