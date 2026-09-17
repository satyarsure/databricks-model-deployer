import { useMemo, useState, type ReactNode } from 'react';
import { Button, Label } from '@databricks/appkit-ui/react';
import { Plus, Trash2, Save, Info } from 'lucide-react';
import { useApiQuery } from '../lib/useApiQuery';
import type { DeploymentRow, PendingDeployment } from '../types';

// ---- shared types -----------------------------------------------------------
type ArtifactType = 's3' | 'uc_volume';
type VariantSource = 'artifact' | 'existing';
type Size = 'SMALL' | 'MEDIUM' | 'LARGE';
interface Artifact {
  label: string;
  // 'artifact' = a new S3/UC-Volume artifact (wrapped + registered as a new version);
  // 'existing' = an already-registered version of the same UC model (referenced as-is,
  // for a champion-vs-challenger A/B test).
  source: VariantSource;
  type: ArtifactType;
  path: string;
  version: string; // used when source === 'existing'
  traffic_percent: number;
}

interface Prefill {
  name: string;
  description: string;
  artifacts: Artifact[];
  inputSchemaText: string;
  outputSchemaText: string;
  experimentName: string;
  evalDataset: string;
  ucCatalog: string;
  ucSchema: string;
  ucModel: string;
  usagePolicy: string;
  tagsText: string;
  permissionsText: string;
  computeType: 'cpu' | 'gpu';
  gpuType: string;
  size: Size;
  scaleToZero: boolean;
}

function safeParse<T>(s: string | null | undefined, fallback: T): T {
  if (!s) return fallback;
  try {
    return JSON.parse(s) as T;
  } catch {
    return fallback;
  }
}

type SchemaParse =
  | { ok: true; fields: Array<{ name: string; type: string }> }
  | { ok: false; error: string };

// Validate a schema entered as a JSON string of [{name, type}, ...].
function parseSchema(text: string, requireNonEmpty: boolean): SchemaParse {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text || '[]');
  } catch {
    return { ok: false, error: 'must be valid JSON' };
  }
  if (!Array.isArray(parsed)) return { ok: false, error: 'must be a JSON array' };
  if (requireNonEmpty && parsed.length === 0)
    return { ok: false, error: 'needs at least one field' };
  const fields: Array<{ name: string; type: string }> = [];
  for (const item of parsed) {
    if (!item || typeof item !== 'object' || Array.isArray(item))
      return { ok: false, error: 'each field must be an object with "name" and "type"' };
    const rec = item as Record<string, unknown>;
    const nm = typeof rec.name === 'string' ? rec.name.trim() : '';
    const ty = typeof rec.type === 'string' ? rec.type.trim() : '';
    if (!nm) return { ok: false, error: 'each field needs a "name"' };
    fields.push({ name: nm, type: ty || 'double' });
  }
  return { ok: true, fields };
}

const PERM_KEYS = ['can_manage', 'can_query', 'can_view'] as const;

// Placeholder / example for the optional serving-endpoint permissions field.
const PERMISSIONS_PLACEHOLDER = `{
  "can_manage": ["greg.mara@databricks.com"],
  "can_query": ["jeff.shmain@databricks.com"],
  "can_view": ["usama.arif@databricks.com"]
}`;

type PermParse =
  | { ok: true; value: Record<string, string[]> | null }
  | { ok: false; error: string };

// Validate the optional serving-endpoint permissions JSON: an object keyed by permission level
// (can_manage / can_query / can_view), each an array of principal strings (user email, group
// name, or service-principal UUID). Empty input is valid (null → omit from the spec).
function parsePermissions(text: string): PermParse {
  const t = (text || '').trim();
  if (!t) return { ok: true, value: null };
  let parsed: unknown;
  try {
    parsed = JSON.parse(t);
  } catch {
    return { ok: false, error: 'must be valid JSON' };
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
    return { ok: false, error: 'must be a JSON object keyed by can_manage / can_query / can_view' };
  // JSON.parse silently keeps only the LAST value for a duplicated key, which would drop
  // principals without warning (e.g. two "can_view" entries). Detect that and reject it so the
  // user merges all principals for a level into one array.
  const rawKeyCount = (t.match(/"can_(?:manage|query|view)"\s*:/g) || []).length;
  const distinctKeyCount = Object.keys(parsed as Record<string, unknown>).filter((k) =>
    (PERM_KEYS as readonly string[]).includes(k),
  ).length;
  if (rawKeyCount > distinctKeyCount)
    return {
      ok: false,
      error: 'has a duplicated permission key — put all principals for a level in one array',
    };
  const out: Record<string, string[]> = {};
  for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
    if (!(PERM_KEYS as readonly string[]).includes(k))
      return { ok: false, error: `unknown key "${k}" (use can_manage, can_query, or can_view)` };
    if (!Array.isArray(v) || v.some((x) => typeof x !== 'string'))
      return { ok: false, error: `"${k}" must be an array of principal strings` };
    const cleaned = (v as string[]).map((s) => s.trim()).filter(Boolean);
    if (cleaned.length) out[k] = cleaned;
  }
  return { ok: true, value: Object.keys(out).length ? out : null };
}

// Turn a previously-deployed row into initial form values for a new version.
function parsePrefill(row: DeploymentRow | null): Prefill | null {
  if (!row) return null;
  const rawArts = safeParse<
    Array<{
      label?: string;
      source?: string;
      type?: string;
      path?: string;
      version?: number | string;
      traffic_percent?: number;
    }>
  >(row.artifacts_json, []);
  const artifacts: Artifact[] =
    Array.isArray(rawArts) && rawArts.length > 0
      ? rawArts.map((a, i) => ({
          label: a.label ?? String.fromCharCode(65 + i),
          source: a.source === 'existing' ? 'existing' : 'artifact',
          type: a.type === 's3' ? 's3' : 'uc_volume',
          path: a.path ?? '',
          version: a.version != null ? String(a.version) : '',
          traffic_percent: Number(a.traffic_percent ?? 0),
        }))
      : [{ label: 'A', source: 'artifact', type: 'uc_volume', path: '', version: '', traffic_percent: 100 }];
  const sizeUpper = (row.compute_size ?? '').toUpperCase();
  const size: Size =
    sizeUpper === 'MEDIUM' || sizeUpper === 'LARGE' ? sizeUpper : 'SMALL';
  return {
    name: row.model_name ?? '',
    description: row.description ?? '',
    artifacts,
    inputSchemaText: row.input_schema_json || '[]',
    outputSchemaText:
      row.output_schema_json || '[\n  { "name": "prediction", "type": "double" }\n]',
    experimentName: row.experiment_name ?? '',
    evalDataset: row.eval_dataset ?? '',
    ucCatalog: row.uc_catalog ?? '',
    ucSchema: row.uc_schema ?? '',
    ucModel: row.uc_model ?? '',
    usagePolicy: row.serverless_usage_policy ?? '',
    tagsText: row.tags ?? '{\n  "team": "mlops"\n}',
    // Only prefill permissions when the stored value is a non-empty object; otherwise leave
    // blank so the placeholder sample shows.
    permissionsText:
      row.permissions_json && row.permissions_json.trim() !== '{}' ? row.permissions_json : '',
    computeType: row.compute_type === 'gpu' ? 'gpu' : 'cpu',
    gpuType: row.gpu_type || 'A10',
    size,
    scaleToZero: row.scale_to_zero == null ? true : row.scale_to_zero === 'true',
  };
}

// Initial form values for an A/B test against an already-deployed model: variant A is
// the existing model's current version (referenced, not re-uploaded), variant B is a new
// artifact. Reuses parsePrefill so name/experiment/UC are pre-filled and locked.
function parseAbBaseline(row: DeploymentRow): Prefill | null {
  const pf = parsePrefill(row);
  if (!pf) return null;
  const currentVersion = (row.model_version ?? '').match(/(\d+)\s*$/)?.[1] ?? '';
  return {
    ...pf,
    artifacts: [
      { label: 'A', source: 'existing', type: 'uc_volume', path: '', version: currentVersion, traffic_percent: 70 },
      { label: 'B', source: 'artifact', type: 'uc_volume', path: '', version: '', traffic_percent: 30 },
    ],
  };
}

const GPU_TYPES = ['A10', 'T4', 'H100'];
const SIZES = ['SMALL', 'MEDIUM', 'LARGE'] as const;

const inputCls =
  'w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring';

// ---- small building blocks --------------------------------------------------
function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-2">
      <div>
        <Label className="text-sm font-semibold text-foreground">{title}</Label>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </div>
      {children}
    </div>
  );
}

function Segmented<T extends string>({
  value,
  options,
  onChange,
  disabled,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  disabled?: boolean;
}) {
  return (
    <div className="inline-flex rounded-md border border-input bg-background p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          disabled={disabled}
          onClick={() => onChange(o.value)}
          className={`rounded px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-50 ${
            value === o.value
              ? 'bg-primary text-primary-foreground'
              : 'text-muted-foreground hover:text-foreground'
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Toggle({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
        checked ? 'bg-primary' : 'bg-muted'
      }`}
    >
      <span
        className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${
          checked ? 'translate-x-5' : 'translate-x-0.5'
        }`}
      />
    </button>
  );
}

// Dropdown of registered versions for a UC model (variant A of an A/B test). Sourced from
// the model_versions analytics query (versions this app has deployed for that model).
function VersionPicker({
  ucFull,
  value,
  onChange,
}: {
  ucFull: string;
  value: string;
  onChange: (v: string) => void;
}) {
  // Versions this app has deployed for the given UC model (from the Lakebase read route).
  const url = useMemo(
    () => (ucFull ? `/api/model-versions?uc_full=${encodeURIComponent(ucFull)}` : null),
    [ucFull],
  );
  const { data, loading } = useApiQuery<{ version: number | string }>(url);
  const versions = (data ?? []).map((r) => String(r.version)).filter(Boolean);
  // Keep the pre-filled current version selectable even if the query hasn't loaded it.
  const options = value && !versions.includes(value) ? [value, ...versions] : versions;
  return (
    <select
      className={inputCls}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={!ucFull}
    >
      {options.length === 0 && (
        <option value="">{loading ? 'Loading versions…' : 'No versions found'}</option>
      )}
      {options.map((v) => (
        <option key={v} value={v}>
          v{v}
        </option>
      ))}
    </select>
  );
}

// ---- main form --------------------------------------------------------------
export function DeployModel({
  prefill,
  abBaseline,
  onDeployed,
  onCancel,
}: {
  prefill?: DeploymentRow | null;
  abBaseline?: DeploymentRow | null;
  onDeployed: (pending?: PendingDeployment) => void;
  onCancel: () => void;
}) {
  const isAb = !!abBaseline;
  const pf = isAb ? parseAbBaseline(abBaseline!) : parsePrefill(prefill ?? null);
  // Both "new version" and "A/B test" lock the model identity (name/experiment/UC).
  const isNewVersion = pf !== null;
  const lockedCls = isNewVersion ? ' opacity-60 cursor-not-allowed' : '';

  // When the UC model is already known (deploying a new version OR an A/B test), a variant
  // can reference an existing registered version instead of a new artifact. The version
  // picker reads the versions from the app's Lakebase route (server resolves the schema).
  const ucFull = (abBaseline ?? prefill)?.uc_full_name ?? '';
  const canUseExisting = isNewVersion && !!ucFull;

  const [name, setName] = useState(pf?.name ?? '');
  const [description, setDescription] = useState(pf?.description ?? '');
  const [artifacts, setArtifacts] = useState<Artifact[]>(
    pf?.artifacts ?? [
      { label: 'A', source: 'artifact', type: 'uc_volume', path: '', version: '', traffic_percent: 100 },
    ],
  );
  const [inputSchemaText, setInputSchemaText] = useState(pf?.inputSchemaText ?? '[]');
  const [outputSchemaText, setOutputSchemaText] = useState(
    pf?.outputSchemaText ?? '[\n  { "name": "prediction", "type": "double" }\n]',
  );
  const [experimentName, setExperimentName] = useState(pf?.experimentName ?? '');
  const [evalDataset, setEvalDataset] = useState(pf?.evalDataset ?? '');
  const [ucCatalog, setUcCatalog] = useState(pf?.ucCatalog ?? '');
  const [ucSchema, setUcSchema] = useState(pf?.ucSchema ?? '');
  const [ucModel, setUcModel] = useState(pf?.ucModel ?? '');
  const [usagePolicy, setUsagePolicy] = useState(pf?.usagePolicy ?? '');
  const [tagsText, setTagsText] = useState(pf?.tagsText ?? '{\n  "team": "mlops"\n}');
  const [permissionsText, setPermissionsText] = useState(pf?.permissionsText ?? '');
  const [computeType, setComputeType] = useState<'cpu' | 'gpu'>(pf?.computeType ?? 'cpu');
  const [gpuType, setGpuType] = useState(pf?.gpuType ?? 'A10');
  const [size, setSize] = useState<Size>(pf?.size ?? 'SMALL');
  const [scaleToZero, setScaleToZero] = useState(pf?.scaleToZero ?? true);

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  const trafficTotal = useMemo(
    () => artifacts.reduce((s, a) => s + (Number(a.traffic_percent) || 0), 0),
    [artifacts],
  );

  const setArtifact = (i: number, patch: Partial<Artifact>) =>
    setArtifacts((prev) => prev.map((a, idx) => (idx === i ? { ...a, ...patch } : a)));
  const addArtifact = () =>
    setArtifacts((prev) => [
      ...prev,
      {
        label: String.fromCharCode(65 + prev.length),
        source: 'artifact',
        type: 'uc_volume',
        path: '',
        version: '',
        traffic_percent: 0,
      },
    ]);
  const removeArtifact = (i: number) =>
    setArtifacts((prev) =>
      prev
        .filter((_, idx) => idx !== i)
        .map((a, idx) => ({ ...a, label: String.fromCharCode(65 + idx) })),
    );

  function validate(): string | null {
    if (!name.trim()) return 'Model name is required.';
    if (artifacts.some((a) => a.source === 'artifact' && !a.path.trim()))
      return 'Every new-artifact variant needs a path.';
    if (artifacts.some((a) => a.source === 'existing' && !/^\d+$/.test(a.version.trim())))
      return 'Every existing-model variant needs a version.';
    if (artifacts.length > 1 && trafficTotal !== 100)
      return `A/B traffic must total 100% (currently ${trafficTotal}%).`;
    const inCheck = parseSchema(inputSchemaText, false);
    if (!inCheck.ok) return `Input schema ${inCheck.error}.`;
    const outCheck = parseSchema(outputSchemaText, true);
    if (!outCheck.ok) return `Output schema ${outCheck.error}.`;
    // Experiment and serverless usage policy are OPTIONAL overrides — when blank, the deploy job
    // uses the environment's configured defaults (var.experiment / var.budget_policy_id).
    if (!ucCatalog.trim() || !ucSchema.trim() || !ucModel.trim())
      return 'UC catalog, schema, and model are all required.';
    try {
      const parsed = JSON.parse(tagsText || '{}');
      if (typeof parsed !== 'object' || Array.isArray(parsed))
        return 'Tags must be a JSON object.';
    } catch {
      return 'Tags must be valid JSON.';
    }
    const permCheck = parsePermissions(permissionsText);
    if (!permCheck.ok) return `Endpoint permissions ${permCheck.error}.`;
    return null;
  }

  async function submit() {
    setError(null);
    setOk(null);
    const problem = validate();
    if (problem) {
      setError(problem);
      return;
    }
    setSubmitting(true);
    try {
      const inParsed = parseSchema(inputSchemaText, false);
      const outParsed = parseSchema(outputSchemaText, true);
      if (!inParsed.ok || !outParsed.ok) {
        setError('Input/Output schema must be valid JSON.');
        setSubmitting(false);
        return;
      }
      const permParsed = parsePermissions(permissionsText);
      if (!permParsed.ok) {
        setError(`Endpoint permissions ${permParsed.error}.`);
        setSubmitting(false);
        return;
      }
      const spec = {
        name: name.trim(),
        description,
        artifacts: artifacts.map((a) =>
          a.source === 'existing'
            ? {
                label: a.label,
                source: 'existing' as const,
                version: a.version.trim(),
                traffic_percent: Number(a.traffic_percent) || 0,
              }
            : {
                label: a.label,
                source: 'artifact' as const,
                type: a.type,
                path: a.path.trim(),
                traffic_percent: Number(a.traffic_percent) || 0,
              },
        ),
        input_schema: inParsed.fields,
        output_schema: outParsed.fields,
        experiment_name: experimentName.trim(),
        eval_dataset: evalDataset.trim(),
        serverless_usage_policy: usagePolicy.trim(),
        tags: JSON.parse(tagsText || '{}'),
        uc: {
          catalog: ucCatalog.trim(),
          schema: ucSchema.trim(),
          model: ucModel.trim(),
        },
        compute: {
          compute_type: computeType,
          gpu_type: computeType === 'gpu' ? gpuType : null,
          size,
          scale_to_zero: scaleToZero,
        },
        // Optional: endpoint ACLs. The submitter gets CAN_MANAGE by default (unless listed here).
        ...(permParsed.value ? { permissions: permParsed.value } : {}),
      };
      const res = await fetch('/api/deploy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(spec),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body.error || `Deploy failed (${res.status})`);
      }
      setOk('Deployment started. Track progress on the Deployed Models tab.');
      // Hand the just-created deployment id back so the Deployed Models tab can show
      // it optimistically before the (cold-starting) job writes its first row.
      const pending: PendingDeployment | undefined = body.deployment_id
        ? {
            deployment_id: String(body.deployment_id),
            model_name: name.trim(),
            uc_full_name: `${ucCatalog.trim()}.${ucSchema.trim()}.${ucModel.trim()}`,
          }
        : undefined;
      setTimeout(() => onDeployed(pending), 900);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  const multi = artifacts.length > 1;

  return (
    <div className="rounded-lg border bg-card p-6 shadow-sm">
      <div className="mx-auto max-w-2xl space-y-7">
        {isNewVersion && (
          <div className="flex items-start gap-2 rounded-md border border-primary/30 bg-primary/5 p-3 text-sm">
            <Info className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
            <div>
              <p className="font-medium text-foreground">
                {isAb
                  ? `A/B test of “${pf?.name}”`
                  : `Deploying a new version of “${pf?.name}”`}
              </p>
              <p className="text-muted-foreground">
                {isAb
                  ? 'Variant A keeps serving an existing version of this model; variant B is a new artifact you provide. Set the traffic split (totals 100%), then Save & Deploy — both are served on the same endpoint and @champion goes to the higher-traffic variant.'
                  : 'Fields are pre-filled from the last deployment. Model name, experiment, and UC model are locked; update the artifact and any other settings, then Save & Deploy.'}
              </p>
            </div>
          </div>
        )}

        <Section title="Model Name">
          <input
            className={inputCls + lockedCls}
            placeholder="e.g. customer_churn_xgb"
            value={name}
            disabled={isNewVersion}
            onChange={(e) => setName(e.target.value)}
          />
        </Section>

        <Section title="Description">
          <textarea
            className={`${inputCls} min-h-[80px]`}
            placeholder="What does this model do?"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </Section>

        <Section
          title="Artifacts"
          hint={
            canUseExisting
              ? 'One or more variants. Each can be a new artifact (S3 or UC Volume) or an existing registered version of this model — switch with the toggle. Add multiple to A/B test with a traffic split that totals 100%.'
              : 'One or more model artifacts (S3 or UC Volume). Add multiple variants to A/B test with a traffic split that totals 100%.'
          }
        >
          <div className="space-y-3">
            {artifacts.map((a, i) => (
              <div key={i} className="rounded-md border border-input p-3">
                <div className="mb-2 flex items-center justify-between">
                  <span className="rounded bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                    Variant {a.label}
                    {a.source === 'existing' ? ' · existing' : ''}
                  </span>
                  {artifacts.length > 1 && (
                    <button
                      type="button"
                      onClick={() => removeArtifact(i)}
                      className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-destructive"
                      aria-label="Remove variant"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  )}
                </div>
                {canUseExisting && (
                  <div className="mb-2">
                    <Segmented<VariantSource>
                      value={a.source}
                      onChange={(v) => setArtifact(i, { source: v })}
                      options={[
                        { value: 'artifact', label: 'New artifact' },
                        { value: 'existing', label: 'Existing version' },
                      ]}
                    />
                  </div>
                )}
                {a.source === 'existing' ? (
                  <div className="space-y-2">
                    <div className="rounded bg-muted/50 px-2 py-1 text-xs text-muted-foreground">
                      Existing deployed model —{' '}
                      <span className="font-mono">{ucFull || '—'}</span>. Pick which version
                      to keep serving as this variant.
                    </div>
                    <div>
                      <Label className="mb-1 block text-xs text-muted-foreground">Version</Label>
                      <VersionPicker
                        ucFull={ucFull}
                        value={a.version}
                        onChange={(v) => setArtifact(i, { version: v })}
                      />
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="mb-2">
                      <Segmented<ArtifactType>
                        value={a.type}
                        onChange={(v) => setArtifact(i, { type: v })}
                        options={[
                          { value: 's3', label: 'S3 Bucket' },
                          { value: 'uc_volume', label: 'UC Volume' },
                        ]}
                      />
                    </div>
                    <input
                      className={inputCls}
                      placeholder={
                        a.type === 's3'
                          ? 's3://bucket/path/model.pkl'
                          : '/Volumes/catalog/schema/volume/model.pkl'
                      }
                      value={a.path}
                      onChange={(e) => setArtifact(i, { path: e.target.value })}
                    />
                  </>
                )}
                {multi && (
                  <div className="mt-2 flex items-center gap-2">
                    <Label className="text-xs text-muted-foreground">Traffic %</Label>
                    <input
                      type="number"
                      min={0}
                      max={100}
                      className={`${inputCls} max-w-[120px]`}
                      value={a.traffic_percent}
                      onChange={(e) =>
                        setArtifact(i, { traffic_percent: Number(e.target.value) })
                      }
                    />
                  </div>
                )}
              </div>
            ))}
          </div>
          <div className="mt-2 flex items-center justify-between">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={addArtifact}
              className="gap-1.5"
            >
              <Plus className="h-4 w-4" /> Add artifact (A/B variant)
            </Button>
            {multi && (
              <span
                className={`text-xs font-medium ${
                  trafficTotal === 100 ? 'text-green-600' : 'text-destructive'
                }`}
              >
                Traffic total: {trafficTotal}%
              </span>
            )}
          </div>
        </Section>

        <Section
          title="Input Schema"
          hint={`JSON array of the model's input columns, e.g. [{"name": "f1", "type": "double"}].`}
        >
          <textarea
            className={`${inputCls} min-h-[96px] font-mono text-xs`}
            placeholder='[{"name": "f1", "type": "double"}]'
            value={inputSchemaText}
            onChange={(e) => setInputSchemaText(e.target.value)}
          />
        </Section>

        <Section
          title="Output Schema"
          hint={`JSON array (at least one field); maps to the MLflow signature outputs, e.g. [{"name": "prediction", "type": "double"}].`}
        >
          <textarea
            className={`${inputCls} min-h-[96px] font-mono text-xs`}
            placeholder='[{"name": "prediction", "type": "double"}]'
            value={outputSchemaText}
            onChange={(e) => setOutputSchemaText(e.target.value)}
          />
        </Section>

        <Section
          title="Experiment name"
          hint="Optional — leave blank to use the environment's configured default MLflow experiment."
        >
          <input
            className={inputCls + lockedCls}
            placeholder="(optional) /Users/you@company.com/experiments/my_experiment"
            value={experimentName}
            disabled={isNewVersion}
            onChange={(e) => setExperimentName(e.target.value)}
          />
        </Section>

        <Section
          title="Evaluation dataset location"
          hint="Optional — leave blank to skip. If given, an S3/UC Volume CSV or Parquet with the model's features plus a target column named like the output field; validated with mlflow.evaluate."
        >
          <input
            className={inputCls}
            placeholder="s3://bucket/eval/dataset.parquet  or  /Volumes/catalog/schema/vol/eval.csv"
            value={evalDataset}
            onChange={(e) => setEvalDataset(e.target.value)}
          />
        </Section>

        <Section
          title="UC Model name"
          hint="Unity Catalog three-level name the model will be registered under."
        >
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div>
              <Label className="text-xs text-muted-foreground">Catalog</Label>
              <input
                className={inputCls + lockedCls}
                placeholder="catalog"
                value={ucCatalog}
                disabled={isNewVersion}
                onChange={(e) => setUcCatalog(e.target.value)}
              />
            </div>
            <div>
              <Label className="text-xs text-muted-foreground">Schema</Label>
              <input
                className={inputCls + lockedCls}
                placeholder="schema"
                value={ucSchema}
                disabled={isNewVersion}
                onChange={(e) => setUcSchema(e.target.value)}
              />
            </div>
            <div>
              <Label className="text-xs text-muted-foreground">Model name</Label>
              <input
                className={inputCls + lockedCls}
                placeholder="model name"
                value={ucModel}
                disabled={isNewVersion}
                onChange={(e) => setUcModel(e.target.value)}
              />
            </div>
          </div>
        </Section>

        <Section
          title="Serverless usage policy"
          hint="Optional — budget policy ID applied to the serving endpoint (set at endpoint creation). Leave blank to use the environment's configured default policy. Find IDs under Settings › Serverless usage policies."
        >
          <input
            className={inputCls}
            placeholder="(optional) budget policy ID (e.g. 1a2b3c4d-…)"
            value={usagePolicy}
            onChange={(e) => setUsagePolicy(e.target.value)}
          />
        </Section>

        <Section title="Tags" hint="JSON-formatted tags applied to the endpoint.">
          <textarea
            className={`${inputCls} min-h-[80px] font-mono text-xs`}
            value={tagsText}
            onChange={(e) => setTagsText(e.target.value)}
          />
        </Section>

        <Section
          title="Endpoint permissions"
          hint="Optional JSON granting others access to the serving endpoint, grouped by level (can_manage / can_query / can_view). Each entry is a user email, a group name, or a service-principal UUID; list all principals for a level in one array. You (the deploying user) get CAN_MANAGE by default unless you list yourself here with a different level, which is then honored (handled server-side, since the deploy job may run as a different identity). Leave blank to grant no one else."
        >
          <textarea
            className={`${inputCls} min-h-[120px] font-mono text-xs`}
            placeholder={PERMISSIONS_PLACEHOLDER}
            value={permissionsText}
            onChange={(e) => setPermissionsText(e.target.value)}
          />
        </Section>

        <Section
          title="Compute"
          hint="Serving compute for this deployment — compute type, size, and idle scale-down."
        >
          <div className="space-y-4 rounded-md border border-input p-4">
            <div>
              <Label className="mb-1.5 block text-xs text-muted-foreground">Type</Label>
              <Segmented<'cpu' | 'gpu'>
                value={computeType}
                onChange={setComputeType}
                options={[
                  { value: 'cpu', label: 'CPU' },
                  { value: 'gpu', label: 'GPU' },
                ]}
              />
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <Label className="mb-1.5 block text-xs text-muted-foreground">
                  GPU Type
                </Label>
                <select
                  className={`${inputCls} disabled:opacity-50`}
                  value={gpuType}
                  disabled={computeType !== 'gpu'}
                  onChange={(e) => setGpuType(e.target.value)}
                >
                  {GPU_TYPES.map((g) => (
                    <option key={g} value={g}>
                      {g}
                    </option>
                  ))}
                </select>
                <p className="mt-1 text-xs text-muted-foreground">Available when Compute is GPU.</p>
              </div>
              <div>
                <Label className="mb-1.5 block text-xs text-muted-foreground">
                  Compute Size
                </Label>
                <select
                  className={inputCls}
                  value={size}
                  onChange={(e) => setSize(e.target.value as (typeof SIZES)[number])}
                >
                  {SIZES.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div className="flex items-center justify-between">
              <div>
                <Label className="text-sm text-foreground">Scale to zero</Label>
                <p className="text-xs text-muted-foreground">
                  Scale the endpoint down to zero instances when idle.
                </p>
              </div>
              <Toggle checked={scaleToZero} onChange={setScaleToZero} />
            </div>
          </div>
        </Section>

        {error && (
          <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
            {error}
          </div>
        )}
        {ok && (
          <div className="rounded-md bg-green-100 p-3 text-sm text-green-700 dark:bg-green-950 dark:text-green-400">
            {ok}
          </div>
        )}

        <div className="flex items-center justify-end gap-3 border-t pt-5">
          <Button type="button" variant="outline" onClick={onCancel} disabled={submitting}>
            Cancel
          </Button>
          <Button type="button" onClick={submit} disabled={submitting} className="gap-2">
            <Save className="h-4 w-4" />
            {submitting
              ? 'Deploying…'
              : isNewVersion
                ? 'Deploy new version'
                : 'Save & Deploy'}
          </Button>
        </div>
      </div>
    </div>
  );
}
