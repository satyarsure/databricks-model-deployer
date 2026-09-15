import {
  Fragment,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from 'react';
import { Button, Skeleton } from '@databricks/appkit-ui/react';
import { useApiQuery } from '../lib/useApiQuery';
import {
  Rocket,
  Search,
  ExternalLink,
  ChevronRight,
  ChevronLeft,
  ChevronDown,
  GitCompare,
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
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium ${cls}`}
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
  // Full date + time (the complete deploy timestamp).
  return d.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
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
  deploymentId,
  live,
  nonce,
}: {
  deploymentId: string;
  live: boolean;
  nonce: number;
}) {
  // The nonce (per poll / page load) is folded into the URL so a change triggers a refetch,
  // streaming new events for an in-progress deployment instead of showing stale data.
  const url = useMemo(
    () =>
      `/api/lifecycle?deployment_id=${encodeURIComponent(deploymentId)}&nonce=${nonce}`,
    [deploymentId, nonce],
  );
  const { data, loading, error } = useApiQuery<LifecycleEvent>(url);
  // Keep the last successful events so a background refetch (each poll tick) doesn't
  // blank the timeline to a skeleton — only newly-arrived events re-render.
  const [events, setEvents] = useState<LifecycleEvent[]>([]);
  useEffect(() => {
    if (data) setEvents((data ?? []) as LifecycleEvent[]);
  }, [data]);

  if (loading && events.length === 0) {
    return (
      <div className="p-3">
        <Skeleton className="h-14 w-64" />
      </div>
    );
  }
  // Keep the last-good events on a transient poll error; only surface it if we have nothing.
  if (error && events.length === 0) {
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

// Page-number items with ellipsis for long ranges (1 … 4 5 6 … 20).
function pageItems(current: number, total: number): Array<number | 'gap'> {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const items: Array<number | 'gap'> = [1];
  const start = Math.max(2, current - 1);
  const end = Math.min(total - 1, current + 1);
  if (start > 2) items.push('gap');
  for (let p = start; p <= end; p++) items.push(p);
  if (end < total - 1) items.push('gap');
  items.push(total);
  return items;
}

// Table footer: page navigation on the left, page-size selector on the right.
function Pagination({
  page,
  totalPages,
  pageSize,
  total,
  onPage,
  onPageSize,
}: {
  page: number;
  totalPages: number;
  pageSize: number;
  total: number;
  onPage: (p: number) => void;
  onPageSize: (n: number) => void;
}) {
  const cell =
    'inline-flex h-8 min-w-[2rem] items-center justify-center rounded-md px-2 text-sm transition-colors';
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3">
      <div className="flex items-center gap-1">
        <button
          type="button"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
          aria-label="Previous page"
          className={`${cell} text-muted-foreground hover:bg-muted disabled:pointer-events-none disabled:opacity-40`}
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
        {pageItems(page, totalPages).map((it, i) =>
          it === 'gap' ? (
            <span key={`gap-${i}`} className="px-1 text-muted-foreground">
              …
            </span>
          ) : (
            <button
              key={it}
              type="button"
              onClick={() => onPage(it)}
              aria-current={it === page ? 'page' : undefined}
              className={`${cell} ${
                it === page
                  ? 'bg-primary text-primary-foreground'
                  : 'text-foreground hover:bg-muted'
              }`}
            >
              {it}
            </button>
          ),
        )}
        <button
          type="button"
          disabled={page >= totalPages}
          onClick={() => onPage(page + 1)}
          aria-label="Next page"
          className={`${cell} text-muted-foreground hover:bg-muted disabled:pointer-events-none disabled:opacity-40`}
        >
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
      <div className="flex items-center gap-3">
        <span className="text-xs text-muted-foreground">{total} total</span>
        <select
          value={pageSize}
          onChange={(e) => onPageSize(Number(e.target.value))}
          aria-label="Rows per page"
          className="rounded-md border border-input bg-background px-2 py-1.5 text-sm outline-none focus:ring-2 focus:ring-ring"
        >
          {[10, 25, 50, 100].map((n) => (
            <option key={n} value={n}>
              {n} / page
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

function DeploymentsTable({
  search,
  expandedIds,
  onToggleExpand,
  onRows,
  onDeployVersion,
  onAbTest,
  pending,
  onResolvePending,
  nonce,
}: {
  search: string;
  expandedIds: Set<string>;
  onToggleExpand: (id: string) => void;
  onRows: (rows: DeploymentRow[]) => void;
  onDeployVersion: (row: DeploymentRow) => void;
  onAbTest: (row: DeploymentRow) => void;
  pending: PendingDeployment | null;
  onResolvePending: () => void;
  nonce: number;
}) {
  // The nonce (per poll / page load) is folded into the URL so a change triggers a refetch,
  // surfacing a just-submitted or in-progress deployment instead of a stale list.
  const url = useMemo(() => `/api/deployments?nonce=${nonce}`, [nonce]);
  const { data, loading, error } = useApiQuery<DeploymentRow>(url);
  // Keep the last successful rows so a background refetch (each poll tick) doesn't blank
  // the table to a skeleton — React then re-renders only the cells whose values changed.
  const [rows, setRows] = useState<DeploymentRow[]>([]);
  useEffect(() => {
    if (data) {
      const r = (data ?? []) as DeploymentRow[];
      setRows(r);
      onRows(r);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  // The real row exists once the deploy job has written it; drop the optimistic one then.
  const pendingResolved =
    pending != null && rows.some((r) => r.deployment_id === pending.deployment_id);

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

  // Client-side pagination (the query caps at 200 rows, so this is cheap).
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  useEffect(() => {
    setPage(1);
  }, [search, pageSize]);
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const pageRows = filtered.slice((safePage - 1) * pageSize, safePage * pageSize);

  // ---- Resizable columns (drag the right edge of a header, Excel-style) -------------------
  // Widths persist per browser. No visible separators — the grab zone only cues on hover.
  const COLS = ['Model Name', 'UC Name', 'Version', 'Deploy Date', 'Status', 'Serving'];
  const DEFAULT_WIDTHS = [220, 280, 90, 210, 150, 140];
  const [widths, setWidths] = useState<number[]>(() => {
    try {
      const raw = localStorage.getItem('modelDeployer.colWidths');
      const parsed = raw ? (JSON.parse(raw) as number[]) : null;
      if (Array.isArray(parsed) && parsed.length === DEFAULT_WIDTHS.length) return parsed;
    } catch {
      /* localStorage unavailable — fall back to defaults */
    }
    return DEFAULT_WIDTHS;
  });
  const resizing = useRef<{ idx: number; startX: number; startW: number } | null>(null);
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      const r = resizing.current;
      if (!r) return;
      const next = Math.max(70, r.startW + (e.clientX - r.startX));
      setWidths((w) => {
        const n = [...w];
        n[r.idx] = next;
        return n;
      });
    };
    const onUp = () => {
      if (!resizing.current) return;
      resizing.current = null;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      setWidths((w) => {
        try {
          localStorage.setItem('modelDeployer.colWidths', JSON.stringify(w));
        } catch {
          /* ignore persistence failures */
        }
        return w;
      });
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, []);
  const startResize = (idx: number) => (e: ReactMouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    resizing.current = { idx, startX: e.clientX, startW: widths[idx] };
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  };
  const tableWidth = widths.reduce((a, b) => a + b, 0);

  if (loading && allRows.length === 0) {
    return (
      <div className="space-y-3 p-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    );
  }

  // Only show the error banner when we have nothing cached to show. A transient poll failure
  // (e.g. a scale-to-zero wake) must not blank an already-populated board — keep the last-good rows.
  if (error && allRows.length === 0) {
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
    <>
    <div className="overflow-x-auto">
      <table
        className="table-fixed text-sm"
        style={{ width: tableWidth, minWidth: '100%' }}
      >
        <colgroup>
          {widths.map((w, i) => (
            <col key={i} style={{ width: w }} />
          ))}
        </colgroup>
        <thead>
          <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
            {COLS.map((label, i) => (
              <th key={label} className="relative whitespace-nowrap px-4 py-3 font-medium">
                {label}
                {/* Drag handle: a transparent grab zone on the column's right edge; it only
                    tints on hover, so no permanent vertical separators are added. */}
                <span
                  role="separator"
                  aria-orientation="vertical"
                  aria-label={`Resize ${label} column`}
                  onMouseDown={startResize(i)}
                  className="absolute right-0 top-0 z-10 h-full w-2 cursor-col-resize select-none hover:bg-primary/30"
                />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {pageRows.map((r) => {
            const ui = endpointUiUrl(r);
            const inProgress = IN_PROGRESS.has(r.status ?? '');
            // In-progress rows are auto-expanded once (see autoOpen in the parent) but stay
            // fully collapsible — the chevron toggles them like any other row.
            const open = expandedIds.has(r.deployment_id);
            return (
              <Fragment key={r.deployment_id}>
              <tr className="border-b last:border-0 hover:bg-muted/40">
                <td className="px-4 py-3">
                  <div className="flex min-w-0 items-center gap-2">
                    <button
                      type="button"
                      onClick={() => onToggleExpand(r.deployment_id)}
                      title={open ? 'Hide lifecycle' : 'Show lifecycle'}
                      className="shrink-0 text-muted-foreground hover:text-foreground"
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
                      title={r.model_name ?? undefined}
                      className="truncate font-medium text-primary hover:underline"
                    >
                      {r.model_name ?? '—'}
                    </button>
                  </div>
                </td>
                <td className="px-4 py-3">
                  <span
                    className="block truncate font-mono text-xs text-muted-foreground"
                    title={r.uc_full_name ?? ''}
                  >
                    {r.uc_full_name ?? '—'}
                  </span>
                </td>
                <td className="whitespace-nowrap px-4 py-3 font-mono text-xs">
                  {r.model_version ?? '—'}
                </td>
                <td className="px-4 py-3 text-muted-foreground tabular-nums">
                  {formatDate(r.deployed_date)}
                </td>
                <td className="px-4 py-3">
                  <StatusBadge status={r.status} />
                  {r.status === 'FAILED' && r.error_message && (
                    <div
                      className="mt-1 truncate text-xs text-muted-foreground"
                      title={r.error_message}
                    >
                      {r.error_message}
                    </div>
                  )}
                </td>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-3 whitespace-nowrap">
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
                    {r.status === 'COMPLETE' && (
                      <button
                        type="button"
                        onClick={() => onAbTest(r)}
                        title="Start an A/B test with this deployed model as variant A"
                        className="inline-flex items-center gap-1 rounded-md border border-input px-2 py-0.5 text-xs text-muted-foreground hover:text-foreground"
                      >
                        <GitCompare className="h-3.5 w-3.5" /> A/B test
                      </button>
                    )}
                  </div>
                </td>
              </tr>
              {open && (
                <tr className="border-b last:border-0 bg-muted/20">
                  <td colSpan={6} className="p-0">
                    <LifecycleTimeline
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
    <Pagination
      page={safePage}
      totalPages={totalPages}
      pageSize={pageSize}
      total={filtered.length}
      onPage={setPage}
      onPageSize={setPageSize}
    />
    </>
  );
}

export function DeployedModels({
  onDeployNew,
  onDeployVersion,
  onAbTest,
  pending,
  onResolvePending,
}: {
  onDeployNew: () => void;
  onDeployVersion: (row: DeploymentRow) => void;
  onAbTest: (row: DeploymentRow) => void;
  pending: PendingDeployment | null;
  onResolvePending: () => void;
}) {
  const [search, setSearch] = useState('');
  // A refresh nonce for the read routes: unique per page load (so a manual refresh always
  // fetches fresh rows, not a list that predates an in-progress deployment) and bumped on
  // each poll tick while something is deploying.
  const [nonce, setNonce] = useState(() => Date.now());
  const [anyInProgress, setAnyInProgress] = useState(false);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  // Deployments we've auto-expanded once (when they first appear in-progress). Tracking this
  // separately lets the user then collapse them without us immediately re-opening on the next poll.
  const autoOpenedRef = useRef<Set<string>>(new Set());

  const toggleExpand = (id: string) =>
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  // Open a deployment's lifecycle exactly once (auto-expand on first sight); the user can then
  // collapse it and it won't spring back open on subsequent polls.
  const autoOpen = (ids: string[]) => {
    const fresh = ids.filter((id) => !autoOpenedRef.current.has(id));
    if (fresh.length === 0) return;
    fresh.forEach((id) => autoOpenedRef.current.add(id));
    setExpandedIds((prev) => {
      const next = new Set(prev);
      fresh.forEach((id) => next.add(id));
      return next;
    });
  };

  // Auto-open a just-submitted deployment (its optimistic row) so its timeline shows immediately.
  useEffect(() => {
    if (pending) autoOpen([pending.deployment_id]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending]);

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

      <DeploymentsTable
        search={search}
        nonce={nonce}
        expandedIds={expandedIds}
        onToggleExpand={toggleExpand}
        onDeployVersion={onDeployVersion}
        onAbTest={onAbTest}
        pending={pending}
        onResolvePending={onResolvePending}
        onRows={(rows) => {
          setAnyInProgress(rows.some((r) => IN_PROGRESS.has(r.status ?? '')));
          autoOpen(
            rows
              .filter((r) => IN_PROGRESS.has(r.status ?? ''))
              .map((r) => r.deployment_id),
          );
        }}
      />
    </div>
  );
}
