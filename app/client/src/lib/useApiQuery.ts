import { useEffect, useState } from 'react';

// Small fetch hook for the app's Lakebase-backed JSON routes (/api/deployments,
// /api/lifecycle, /api/model-versions). Mirrors the {data, loading, error} shape the
// pages previously got from useAnalyticsQuery: `data` is the row array (or null before the
// first load). Re-runs whenever `url` changes — the pages fold a refresh nonce into the URL,
// so a changing nonce triggers a refetch (the live-poll behavior).
export function useApiQuery<T>(
  url: string | null,
): { data: T[] | null; loading: boolean; error: string | null } {
  const [data, setData] = useState<T[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!url) {
      setData(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    fetch(url)
      .then(async (r) => {
        if (!r.ok) {
          const body = (await r.json().catch(() => ({}))) as { error?: string };
          throw new Error(body.error || `HTTP ${r.status}`);
        }
        return r.json();
      })
      .then((d) => {
        if (cancelled) return;
        setData(Array.isArray(d) ? (d as T[]) : []);
        setError(null);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [url]);

  return { data, loading, error };
}
