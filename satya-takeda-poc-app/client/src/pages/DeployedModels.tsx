import { useEffect, useMemo, useState } from 'react';
import {
  useAnalyticsQuery,
  Button,
  Skeleton,
} from '@databricks/appkit-ui/react';
import { Rocket, Search, ExternalLink } from 'lucide-react';
import type { DeploymentRow } from '../types';

const IN_PROGRESS = new Set(['IN_PROGRESS', 'VALIDATING', 'DEPLOYING']);

function StatusBadge({ status }: { status: string | null }) {
  const s = status ?? '';
  let cls = 'bg-muted text-muted-foreground';
  let label: string = s || '—';
  if (s === 'COMPLETE') {
    cls = 'bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-400';
    label = 'Complete';
  } else if (IN_PROGRESS.has(s)) {
    cls = 'bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-400';
    label = 'Deploy in progress';
  } else if (s === 'FAILED') {
    cls = 'bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-400';
    label = 'Failed';
  }
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${cls}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current opacity-70" />
      {label}
    </span>
  );
}

function endpointUiUrl(row: DeploymentRow): string | null {
  if (!row.invoke_url || !row.endpoint_name) return null;
  try {
    const origin = new URL(row.invoke_url).origin;
    return `${origin}/ml/endpoints/${row.endpoint_name}`;
  } catch {
    return null;
  }
}

function formatDate(v: string | null): string {
  if (!v) return '—';
  const d = new Date(v.replace(' ', 'T'));
  if (Number.isNaN(d.getTime())) return v;
  return d.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

function DeploymentsTable({
  search,
  onRows,
  onDeployVersion,
}: {
  search: string;
  onRows: (rows: DeploymentRow[]) => void;
  onDeployVersion: (row: DeploymentRow) => void;
}) {
  const { data, loading, error } = useAnalyticsQuery('deployments', {});
  const rows = (data ?? []) as DeploymentRow[];

  useEffect(() => {
    if (data) onRows(rows);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(
      (r) =>
        (r.model_name ?? '').toLowerCase().includes(q) ||
        (r.uc_full_name ?? '').toLowerCase().includes(q),
    );
  }, [rows, search]);

  if (loading && rows.length === 0) {
    return (
      <div className="space-y-3 p-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className="m-4 rounded-md bg-destructive/10 p-3 text-sm text-destructive">
        Failed to load deployments: {error}
      </div>
    );
  }

  if (filtered.length === 0) {
    const searching = search.trim().length > 0;
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
        <p className="text-sm font-medium text-foreground">
          {searching ? `No models match “${search.trim()}”` : 'No models deployed yet'}
        </p>
        <p className="text-sm text-muted-foreground">
          {searching
            ? 'Try a different name, or clear the search.'
            : 'Use “Deploy new model” to publish your first model to serving.'}
        </p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
            <th className="px-4 py-3 font-medium">Model Name</th>
            <th className="px-4 py-3 font-medium">UC Name</th>
            <th className="px-4 py-3 font-medium">Version</th>
            <th className="px-4 py-3 font-medium">Deploy Date</th>
            <th className="px-4 py-3 font-medium">Status</th>
            <th className="px-4 py-3 font-medium">Serving</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((r) => {
            const ui = endpointUiUrl(r);
            return (
              <tr key={r.deployment_id} className="border-b last:border-0 hover:bg-muted/40">
                <td className="px-4 py-3">
                  <button
                    type="button"
                    onClick={() => onDeployVersion(r)}
                    title="Deploy a new version of this model"
                    className="font-medium text-primary hover:underline"
                  >
                    {r.model_name ?? '—'}
                  </button>
                </td>
                <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
                  {r.uc_full_name ?? '—'}
                </td>
                <td className="px-4 py-3">{r.model_version ?? '—'}</td>
                <td className="px-4 py-3 text-muted-foreground">
                  {formatDate(r.deployed_date)}
                </td>
                <td className="px-4 py-3">
                  <StatusBadge status={r.status} />
                  {r.status === 'FAILED' && r.error_message && (
                    <div
                      className="mt-1 max-w-xs truncate text-xs text-muted-foreground"
                      title={r.error_message}
                    >
                      {r.error_message}
                    </div>
                  )}
                </td>
                <td className="px-4 py-3">
                  {r.status === 'COMPLETE' && ui ? (
                    <a
                      href={ui}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-primary hover:underline"
                    >
                      Open <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function DeployedModels({
  onDeployNew,
  onDeployVersion,
}: {
  onDeployNew: () => void;
  onDeployVersion: (row: DeploymentRow) => void;
}) {
  const [search, setSearch] = useState('');
  const [refreshTick, setRefreshTick] = useState(0);
  const [anyInProgress, setAnyInProgress] = useState(false);

  // Poll for status changes while any deployment is still running.
  useEffect(() => {
    if (!anyInProgress) return;
    const id = setInterval(() => setRefreshTick((t) => t + 1), 5000);
    return () => clearInterval(id);
  }, [anyInProgress]);

  return (
    <div className="rounded-lg border bg-card shadow-sm">
      <div className="flex flex-wrap items-center gap-3 p-4">
        <div className="relative flex-1 min-w-[220px]">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by model name or UC name (contains)…"
            className="w-full rounded-md border border-input bg-background py-2 pl-9 pr-3 text-sm outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <Button onClick={onDeployNew} className="gap-2">
          <Rocket className="h-4 w-4" />
          Deploy new model
        </Button>
      </div>

      <DeploymentsTable
        key={refreshTick}
        search={search}
        onDeployVersion={onDeployVersion}
        onRows={(rows) =>
          setAnyInProgress(rows.some((r) => IN_PROGRESS.has(r.status ?? '')))
        }
      />
    </div>
  );
}
