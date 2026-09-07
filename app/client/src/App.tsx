import { useEffect, useState } from 'react';
import { Rocket } from 'lucide-react';
import { DeployedModels } from './pages/DeployedModels';
import { DeployModel } from './pages/DeployModel';
import type { DeploymentRow, PendingDeployment } from './types';

type Tab = 'deployed' | 'deploy';

// A just-submitted deployment is shown optimistically until the deploy job writes its
// first row. Persist it so a hard page reload during the job's cold-start window (before
// any DB row exists) still shows the in-progress deployment instead of dropping it.
const PENDING_KEY = 'modelDeployer.pending';
const PENDING_TTL_MS = 20 * 60 * 1000; // stop showing a stale optimistic row after 20 min

function loadPending(): PendingDeployment | null {
  try {
    const raw = sessionStorage.getItem(PENDING_KEY);
    if (!raw) return null;
    const p = JSON.parse(raw) as PendingDeployment & { _ts?: number };
    if (!p.deployment_id || (p._ts && Date.now() - p._ts > PENDING_TTL_MS)) {
      sessionStorage.removeItem(PENDING_KEY);
      return null;
    }
    return {
      deployment_id: p.deployment_id,
      model_name: p.model_name,
      uc_full_name: p.uc_full_name,
    };
  } catch {
    return null;
  }
}

function savePending(p: PendingDeployment | null) {
  try {
    if (p) sessionStorage.setItem(PENDING_KEY, JSON.stringify({ ...p, _ts: Date.now() }));
    else sessionStorage.removeItem(PENDING_KEY);
  } catch {
    /* sessionStorage unavailable — optimistic row just won't survive a reload */
  }
}

function tabClass(active: boolean) {
  return `relative px-1 pb-3 pt-2 text-sm font-medium transition-colors ${
    active
      ? 'text-foreground after:absolute after:inset-x-0 after:-bottom-px after:h-0.5 after:bg-primary'
      : 'text-muted-foreground hover:text-foreground'
  }`;
}

export default function App() {
  const [tab, setTab] = useState<Tab>('deployed');
  const [prefill, setPrefill] = useState<DeploymentRow | null>(null);
  const [abBaseline, setAbBaseline] = useState<DeploymentRow | null>(null);
  const [pending, setPendingState] = useState<PendingDeployment | null>(() => loadPending());
  const [email, setEmail] = useState('');

  // Keep sessionStorage in sync so the optimistic row survives a reload.
  const setPending = (p: PendingDeployment | null) => {
    setPendingState(p);
    savePending(p);
  };

  useEffect(() => {
    fetch('/api/whoami')
      .then((r) => r.json())
      .then((d) => setEmail(d.email ?? ''))
      .catch(() => {});
  }, []);

  return (
    <div className="min-h-screen bg-background flex flex-col">
      <header className="border-b bg-card px-4 md:px-8 py-4">
        <div className="max-w-6xl mx-auto flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <Rocket className="h-5 w-5" />
          </div>
          <div className="flex-1">
            <h1 className="text-lg font-semibold leading-tight text-foreground">
              Model Deployer
            </h1>
            <p className="text-sm text-muted-foreground">
              Deploy model artifacts to Databricks Model Serving
            </p>
          </div>
          {email && (
            <span className="hidden sm:inline text-xs text-muted-foreground">
              {email}
            </span>
          )}
        </div>
      </header>

      <div className="border-b px-4 md:px-8">
        <div className="max-w-6xl mx-auto flex gap-6">
          <button
            type="button"
            className={tabClass(tab === 'deployed')}
            onClick={() => setTab('deployed')}
          >
            Deployed Models
          </button>
          <button
            type="button"
            className={tabClass(tab === 'deploy')}
            onClick={() => setTab('deploy')}
          >
            {abBaseline
              ? `A/B Test — ${abBaseline.model_name}`
              : prefill
                ? `Deploy Model — ${prefill.model_name}`
                : 'Deploy Model'}
          </button>
        </div>
      </div>

      <main className="flex-1 p-4 md:p-8">
        <div className="max-w-6xl mx-auto">
          {tab === 'deployed' ? (
            <DeployedModels
              pending={pending}
              onResolvePending={() => setPending(null)}
              onDeployNew={() => {
                setPrefill(null);
                setAbBaseline(null);
                setTab('deploy');
              }}
              onDeployVersion={(row) => {
                setAbBaseline(null);
                setPrefill(row);
                setTab('deploy');
              }}
              onAbTest={(row) => {
                setPrefill(null);
                setAbBaseline(row);
                setTab('deploy');
              }}
            />
          ) : (
            <DeployModel
              key={abBaseline ? `ab-${abBaseline.deployment_id}` : (prefill?.deployment_id ?? 'new')}
              prefill={prefill}
              abBaseline={abBaseline}
              onDeployed={(p) => {
                setPrefill(null);
                setAbBaseline(null);
                setPending(p ?? null);
                setTab('deployed');
              }}
              onCancel={() => {
                setPrefill(null);
                setAbBaseline(null);
                setTab('deployed');
              }}
            />
          )}
        </div>
      </main>
    </div>
  );
}
