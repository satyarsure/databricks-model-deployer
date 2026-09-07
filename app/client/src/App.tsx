import { useEffect, useState } from 'react';
import { Rocket } from 'lucide-react';
import { DeployedModels } from './pages/DeployedModels';
import { DeployModel } from './pages/DeployModel';
import type { DeploymentRow, PendingDeployment } from './types';

type Tab = 'deployed' | 'deploy';

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
  const [pending, setPending] = useState<PendingDeployment | null>(null);
  const [email, setEmail] = useState('');

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
            {prefill ? `Deploy Model — ${prefill.model_name}` : 'Deploy Model'}
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
                setTab('deploy');
              }}
              onDeployVersion={(row) => {
                setPrefill(row);
                setTab('deploy');
              }}
            />
          ) : (
            <DeployModel
              key={prefill?.deployment_id ?? 'new'}
              prefill={prefill}
              onDeployed={(p) => {
                setPrefill(null);
                setPending(p ?? null);
                setTab('deployed');
              }}
              onCancel={() => {
                setPrefill(null);
                setTab('deployed');
              }}
            />
          )}
        </div>
      </main>
    </div>
  );
}
