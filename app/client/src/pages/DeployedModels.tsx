import { Fragment, useEffect, useMemo, useState } from 'react';
import {
  useAnalyticsQuery,
  Button,
  Skeleton,
} from '@databricks/appkit-ui/react';
import { sql } from '@databricks/appkit-ui/js';
import {
  Rocket,
  Search,
  ExternalLink,
  ChevronRight,
  ChevronDown,
} from 'lucide-react';
import type { DeploymentRow, PendingDeployment } from '../types';

const IN_PROGRESS = new Set(['IN_PROGRESS', 'VALIDATING', 'DEPLOYING']);

// A placeholder row for a just-submitted deployment, shown immediately (auto-expanded,
// "Deploy in progress") until the cold-starting deploy job writes its first real row.
function makeOptimisticRow(p: PendingDeployment): DeploymentRow {
  return {
    deployment_id: p.deployment_id,
    model_name: p.model_name,
    description: null,
    uc_full_name: p.uc_full_name,
    uc_catalog: null,
    uc_schema: null,
    uc_model: null,
    model_version: null,
    status: 'IN_PROGRESS',
    stage: 'wrapper',
    error_message: null,
    endpoint_name: null,
    invoke_url: null,
    experiment_name: null,
    eval_dataset: null,
    serverless_usage_policy: null,
    tags: null,
    artifacts_json: null,
    input_schema_json: null,
    output_schema_json: null,
    compute_type: null,
    gpu_type: null,
    compute_size: null,
    scale_to_zero: null,
    deployed_by: null,
    deployed_date: new Date().toISOString(),
    updated_at: null,
  };
}

interface LifecycleEvent {
  stage: string | null;
  status: string | null;
  message: string | null;
  event_time: string | null;
}

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

function formatTime(v: string | null): string {
  if (!v) return '';
  const d = new Date(v.replace(' ', 'T'));
  if (Number.isNaN(d.getTime())) return v;
  return d.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function eventDotClass(status: string | null): string {
  if (status === 'COMPLETE') return 'bg-green-500';
  if (status === 'FAILED') return 'bg-red-500';
  if (IN_PROGRESS.has(status ?? '')) return 'bg-amber-500';
  return 'bg-muted-foreground';
}

// Vertical timeline of a deployment's lifecycle events (wrapper -> validator -> deployer).
function LifecycleTimeline({
  lifecycleTable,
  deploymentId,
  live,
  nonce,
}: {
  lifecycleTable: string;
  deploymentId: string;
  live: boolean;
  nonce: number;
}) {
  // Changing nonce (per poll / page load) re-executes the query and busts the cache,
  // so an in-progress deployment's timeline streams new events instead of showing stale data.
  const params = useMemo(
    () => ({
      lifecycle_table: sql.string(lifecycleTable),
      deployment_id: sql.string(deploymentId),
      refresh_nonce: sql.string(String(nonce)),
    }),
    [lifecycleTable, deploymentId, nonce],
  );
  const { data, loading, error } = useAnalyticsQuery('lifecycle', params);
  const events = (data ?? []) as LifecycleEvent[];

  if (loading && events.length === 0) {
    return (
      <div className="p-3">
        <Skeleton className="h-14 w-64" />
      </div>
    );
  }
  if (error) {
    return (
      <div className="p-3 text-xs text-destructive">Failed to load lifecycle: {error}</div>
    );
  }
  if (events.length === 0) {
    return (
      <div className="p-3 text-xs text-muted-foreground">
        Waiting for the first lifecycle event…
      </div>
    );
  }

  return (
    <div className="px-4 py-3">
      <div className="mb-2 flex items-center gap-2 text-xs font-medium text-muted-foreground">
        Deployment lifecycle
        {live && (
          <span className="inline-flex items-center gap-1 text-amber-600 dark:text-amber-400">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
            live
          </span>
        )}
      </div>
      <ol className="relative ml-1 space-y-3 border-l pl-4">
        {events.map((e, i) => (
          <li key={i} className="relative">
            <span
              className={`absolute -left-[21px] top-1 h-2.5 w-2.5 rounded-full ring-2 ring-card ${eventDotClass(e.status)}`}
            />
            <div className="flex items-center gap-2">
              <span className="text-xs font-medium capitalize text-foreground">
                {e.stage ?? '—'}
              </span>
              <span className="text-xs text-muted-foreground">{e.status}</span>
              <span className="ml-auto text-xs tabular-nums text-muted-foreground">
                {formatTime(e.event_time)}
              </span>
            </div>
            {e.message && (
              <div className="max-w-xl truncate text-xs text-muted-foreground" title={e.message}>
                {e.message}
              </div>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

function DeploymentsTable({
  search,
  deploymentsTable,
  lifecycleTable,
  expandedIds,
  onToggleExpand,
  onRows,
  onDeployVersion,
  pending,
  onResolvePending,
  nonce,
}: {
  search: string;
  deploymentsTable: string;
  lifecycleTable: string;
  expandedIds: Set<string>;
  onToggleExpand: (id: string) => void;
  onRows: (rows: DeploymentRow[]) => void;
  onDeployVersion: (row: DeploymentRow) => void;
  pending: PendingDeployment | null;
  onResolvePending: () => void;
  nonce: number;
}) {
  // Changing nonce (per poll / page load) re-executes the query and busts the cache,
  // so a just-submitted or in-progress deployment shows up instead of a stale list.
  const params = useMemo(
    () => ({
      deployments_table: sql.string(deploymentsTable),
      refresh_nonce: sql.string(String(nonce)),
    }),
    [deploymentsTable, nonce],
  );
  const { data, loading, error } = useAnalyticsQuery('deployments', params);
  const rows = (data ?? []) as DeploymentRow[];

  // The real row exists once the deploy job has written it; drop the optimistic one then.
  const pendingResolved =
    pending != null && rows.some((r) => r.deployment_id === pending.deployment_id);

  useEffect(() => {
    if (data) onRows(rows);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  useEffect(() => {
    if (pendingResolved) onResolvePending();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingResolved]);

  const allRows =
    pending && !pendingResolved ? [makeOptimisticRow(pending), ...rows] : rows;

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return allRows;
    return allRows.filter(
      (r) =>
        (r.model_name ?? '').toLowerCase().includes(q) ||
        (r.uc_full_name ?? '').toLowerCase().includes(q),
    );
  }, [allRows, search]);

  if (loading && allRows.length === 0) {
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
            const inProgress = IN_PROGRESS.has(r.status ?? '');
            const open = inProgress || expandedIds.has(r.deployment_id);
            return (
              <Fragment key={r.deployment_id}>
              <tr className="border-b last:border-0 hover:bg-muted/40">
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => onToggleExpand(r.deployment_id)}
                      title={open ? 'Hide lifecycle' : 'Show lifecycle'}
                      className="text-muted-foreground hover:text-foreground"
                      aria-label="Toggle lifecycle"
                    >
                      {open ? (
                        <ChevronDown className="h-4 w-4" />
                      ) : (
                        <ChevronRight className="h-4 w-4" />
                      )}
                    </button>
                    <button
                      type="button"
                      onClick={() => onDeployVersion(r)}
                      title="Deploy a new version of this model"
                      className="font-medium text-primary hover:underline"
                    >
                      {r.model_name ?? '—'}
                    </button>
                  </div>
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
              {open && (
                <tr className="border-b last:border-0 bg-muted/20">
                  <td colSpan={6} className="p-0">
                    <LifecycleTimeline
                      lifecycleTable={lifecycleTable}
                      deploymentId={r.deployment_id}
                      live={inProgress}
                      nonce={nonce}
                    />
                  </td>
                </tr>
              )}
              </Fragment>
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
  pending,
  onResolvePending,
}: {
  onDeployNew: () => void;
  onDeployVersion: (row: DeploymentRow) => void;
  pending: PendingDeployment | null;
  onResolvePending: () => void;
}) {
  const [search, setSearch] = useState('');
  // A cache-busting nonce for the analytics queries: unique per page load (so a manual
  // refresh always fetches fresh rows, not a cached list that predates an in-progress
  // deployment) and bumped on each poll tick while something is deploying.
  const [nonce, setNonce] = useState(() => Date.now());
  const [anyInProgress, setAnyInProgress] = useState(false);
  const [deploymentsTable, setDeploymentsTable] = useState<string | null>(null);
  const [lifecycleTable, setLifecycleTable] = useState<string | null>(null);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  const toggleExpand = (id: string) =>
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  // Resolve which catalog.schema tables to read (from the server, which derives them
  // from the bound deploy job — nothing hardcoded in the client).
  useEffect(() => {
    fetch('/api/config')
      .then((r) => r.json())
      .then((d) => {
        setDeploymentsTable(d.deploymentsTable);
        setLifecycleTable(d.lifecycleTable);
      })
      .catch(() => {
        setDeploymentsTable('main.default.model_deployments');
        setLifecycleTable('main.default.model_lifecycle_events');
      });
  }, []);

  // Poll for status changes while any deployment is still running — or while a
  // just-submitted deployment is still waiting for the job to write its first row.
  useEffect(() => {
    if (!anyInProgress && !pending) return;
    const id = setInterval(() => setNonce(Date.now()), 5000);
    return () => clearInterval(id);
  }, [anyInProgress, pending]);

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

      {deploymentsTable === null || lifecycleTable === null ? (
        <div className="space-y-3 p-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : (
        <DeploymentsTable
          key={deploymentsTable}
          search={search}
          deploymentsTable={deploymentsTable}
          lifecycleTable={lifecycleTable}
          nonce={nonce}
          expandedIds={expandedIds}
          onToggleExpand={toggleExpand}
          onDeployVersion={onDeployVersion}
          pending={pending}
          onResolvePending={onResolvePending}
          onRows={(rows) =>
            setAnyInProgress(rows.some((r) => IN_PROGRESS.has(r.status ?? '')))
          }
        />
      )}
    </div>
  );
}
