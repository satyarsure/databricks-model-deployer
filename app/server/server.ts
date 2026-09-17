import { createApp, jobs, server, lakebase } from '@databricks/appkit';
import { z } from 'zod';

// The deploy job owns this Postgres schema and writes the two app tables into it; the app
// reads them from Lakebase (sub-second, real-time). Overridable per environment.
const PG_SCHEMA = process.env.PG_APP_SCHEMA || 'model_deployer';

// ---- Deploy spec validation -------------------------------------------------
const fieldSchema = z.object({
  name: z.string().min(1),
  type: z.string().min(1),
});

// A variant is either a NEW artifact (S3/UC Volume path, wrapped+registered as a new
// version) or an EXISTING already-registered version of the same UC model (referenced
// as-is for a champion-vs-challenger A/B test — no re-wrap).
const artifactSchema = z
  .object({
    label: z.string().optional(),
    source: z.enum(['artifact', 'existing']).default('artifact'),
    type: z.enum(['s3', 'uc_volume']).optional(),
    path: z.string().optional(),
    version: z.union([z.number(), z.string()]).optional(),
    traffic_percent: z.number().min(0).max(100).optional(),
  })
  .superRefine((a, ctx) => {
    // model_version is stored as a comma-joined "label:version" string, so a comma in a label
    // would corrupt version parsing (in the list and the A/B version picker). Disallow it.
    if (a.label && a.label.includes(','))
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'label cannot contain a comma',
        path: ['label'],
      });
    if (a.source === 'existing') {
      const v = a.version == null ? '' : String(a.version).trim();
      if (!v || !/^\d+$/.test(v))
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: 'existing variant needs a numeric version',
          path: ['version'],
        });
    } else {
      if (!a.type)
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: 'artifact variant needs a type',
          path: ['type'],
        });
      if (!a.path || !a.path.trim())
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: 'artifact variant needs a path',
          path: ['path'],
        });
    }
  });

const deploySpecSchema = z.object({
  name: z.string().min(1),
  description: z.string().optional().default(''),
  artifacts: z.array(artifactSchema).min(1),
  input_schema: z.array(fieldSchema).default([]),
  output_schema: z.array(fieldSchema).min(1),
  // Optional: when blank, the deploy job falls back to the bundle defaults (var.experiment /
  // var.budget_policy_id) configured for the environment.
  experiment_name: z.string().optional().default(''),
  eval_dataset: z.string().optional().default(''),
  serverless_usage_policy: z.string().optional().default(''),
  tags: z.record(z.string(), z.any()).default({}),
  // Optional serving-endpoint access control, grouped by permission level. Each entry is a
  // principal: an email (user), a 36-char UUID (service principal), or a name (group). The UI
  // submitter is granted CAN_MANAGE by the deploy job BY DEFAULT — unless they are listed here
  // with a level, which is then honored. Applied by the deploy notebook after the endpoint exists.
  permissions: z
    .object({
      can_manage: z.array(z.string()).optional(),
      can_query: z.array(z.string()).optional(),
      can_view: z.array(z.string()).optional(),
    })
    .optional(),
  uc: z.object({
    catalog: z.string().min(1),
    schema: z.string().min(1),
    model: z.string().min(1),
  }),
  compute: z.object({
    compute_type: z.enum(['cpu', 'gpu']),
    gpu_type: z.string().nullable().optional(),
    size: z.enum(['SMALL', 'MEDIUM', 'LARGE']),
    scale_to_zero: z.boolean().default(true),
  }),
  endpoint_name: z.string().optional(),
});

createApp({
  plugins: [
    jobs({
      jobs: {
        // Single notebook deploy job; the plugin forwards these as notebook_params.
        default: {
          taskType: 'notebook',
          params: z.object({
            deploy_spec: z.string(),
            deployment_id: z.string(),
          }),
        },
      },
    }),
    server(),
    lakebase(),
  ],
  async onPluginsReady(appkit) {
    appkit.server.extend((app) => {
      // ---- Lakebase reads (the app's data store) --------------------------------------
      // The deploy job owns the `model_deployer` schema and writes these tables; the app
      // reads them (sub-second, real-time). Reads tolerate "not created / not granted yet"
      // (before the first job run or the SP grant) by returning an empty list, so the board
      // shows an empty state instead of an error.
      const DEPLOYMENTS = `${PG_SCHEMA}.model_deployments`;
      const LIFECYCLE = `${PG_SCHEMA}.model_lifecycle_events`;
      // undefined_table / invalid_schema_name / insufficient_privilege -> treat as "empty".
      const PG_EMPTY_CODES = new Set(['42P01', '3F000', '42501']);
      const pgErr = (e: unknown) => (e as { code?: string; message?: string }) ?? {};
      const isEmpty = (e: unknown) => PG_EMPTY_CODES.has(pgErr(e).code ?? '');
      // Log (but still return []) when reads are blocked by a *permission* error — that most
      // likely means the app SP was never granted (app_sp unset / job not re-run), which would
      // otherwise be indistinguishable from an empty system.
      const noteEmpty = (route: string, e: unknown) => {
        if (pgErr(e).code === '42501') {
          console.warn(
            `[lakebase] ${route}: permission denied on ${PG_SCHEMA} — the app SP may not be granted ` +
              `SELECT (set app_sp in the deploy job and run one deployment). Returning [].`,
          );
        }
      };

      // Lakebase can drop an idle connection or briefly refuse during a scale-to-zero wake, so
      // retry once on a transient/connection error (a fresh pool connection recovers). Query
      // errors (missing table, permission, bad SQL) are not retried. Reads and the ON CONFLICT
      // DO NOTHING insert are idempotent, so a retry is safe.
      const pgQuery = async (sqlText: string, params: unknown[] = []) => {
        try {
          return await appkit.lakebase.query(sqlText, params);
        } catch (e) {
          const code = pgErr(e).code ?? '';
          const transient =
            !code ||
            ['57P01', '08000', '08003', '08006', '08P01'].includes(code) ||
            /terminat|reset|refused|timeout|ECONN|EPIPE|closed/i.test(String(pgErr(e).message ?? ''));
          if (!transient) throw e;
          console.warn('[lakebase] transient error; retrying once:', pgErr(e).message ?? e);
          return await appkit.lakebase.query(sqlText, params);
        }
      };

      app.get('/api/deployments', async (_req, res) => {
        try {
          const { rows } = await pgQuery(
            `SELECT deployment_id::text, model_name, description, uc_full_name, uc_catalog,
                    uc_schema, uc_model, model_version, status, stage, error_message,
                    endpoint_name, invoke_url, experiment_name, eval_dataset,
                    serverless_usage_policy, tags, artifacts_json, input_schema_json,
                    output_schema_json, permissions_json, compute_type, gpu_type, compute_size,
                    scale_to_zero::text, deployed_by, deployed_date, updated_at
             FROM ${DEPLOYMENTS}
             ORDER BY deployed_date DESC NULLS LAST, updated_at DESC NULLS LAST
             LIMIT 200`,
          );
          res.json(rows);
        } catch (e) {
          if (isEmpty(e)) { noteEmpty('/api/deployments', e); res.json([]); return; }
          console.error('[lakebase] /api/deployments failed:', e);
          res.status(500).json({ error: String(pgErr(e).message ?? e) });
        }
      });

      app.get('/api/lifecycle', async (req, res) => {
        const deploymentId = String(req.query.deployment_id ?? '');
        if (!/^\d+$/.test(deploymentId)) { res.json([]); return; }
        try {
          const { rows } = await pgQuery(
            `SELECT stage, status, message, event_time
             FROM ${LIFECYCLE} WHERE deployment_id = $1::bigint ORDER BY event_time`,
            [deploymentId],
          );
          res.json(rows);
        } catch (e) {
          if (isEmpty(e)) { noteEmpty('/api/lifecycle', e); res.json([]); return; }
          console.error('[lakebase] /api/lifecycle failed:', e);
          res.status(500).json({ error: String(pgErr(e).message ?? e) });
        }
      });

      app.get('/api/model-versions', async (req, res) => {
        const ucFull = String(req.query.uc_full ?? '');
        if (!ucFull) { res.json([]); return; }
        try {
          const { rows } = await pgQuery(
            `SELECT DISTINCT (substring(trim(v) FROM '([0-9]+)$'))::int AS version
             FROM ${DEPLOYMENTS} d,
                  LATERAL regexp_split_to_table(COALESCE(d.model_version, ''), ',') AS v
             WHERE d.uc_full_name = $1 AND d.status = 'COMPLETE'
               AND substring(trim(v) FROM '([0-9]+)$') IS NOT NULL
             ORDER BY version DESC`,
            [ucFull],
          );
          res.json(rows);
        } catch (e) {
          if (isEmpty(e)) { noteEmpty('/api/model-versions', e); res.json([]); return; }
          console.error('[lakebase] /api/model-versions failed:', e);
          res.status(500).json({ error: String(pgErr(e).message ?? e) });
        }
      });

      // Signed-in user (for header display).
      app.get('/api/whoami', (req, res) => {
        const email =
          (req.headers['x-forwarded-email'] as string) ||
          (req.headers['x-forwarded-user'] as string) ||
          '';
        res.json({ email });
      });

      // Kick off a deployment: validate the spec, trigger the deploy Job, and write an initial
      // record to Lakebase Postgres (below) so the UI shows a real row immediately. The Job then
      // upserts status rows into the same Lakebase tables, which the UI reads via /api/deployments.
      app.post('/api/deploy', async (req, res) => {
        const parsed = deploySpecSchema.safeParse(req.body);
        if (!parsed.success) {
          res
            .status(400)
            .json({ error: 'Invalid deployment spec', details: parsed.error.issues });
          return;
        }
        const spec = parsed.data;

        // A/B traffic must total 100 when there is more than one artifact.
        if (spec.artifacts.length > 1) {
          const total = spec.artifacts.reduce(
            (s, a) => s + (a.traffic_percent ?? 0),
            0,
          );
          if (total !== 100) {
            res
              .status(400)
              .json({ error: `A/B traffic must total 100% (got ${total}%)` });
            return;
          }
        }

        // Attach submitting user and a client-generated (JS-safe, ms) deployment id.
        const email =
          (req.headers['x-forwarded-email'] as string) ||
          (req.headers['x-forwarded-user'] as string) ||
          'unknown';
        const deploymentId = Date.now();
        const fullSpec = { ...spec, deployed_by: email };

        const result = await appkit.jobs('default').runNow({
          deploy_spec: JSON.stringify(fullSpec),
          deployment_id: String(deploymentId),
        });
        if (!result.ok) {
          res
            .status(502)
            .json({ error: `Failed to trigger deploy job: ${result.message}` });
          return;
        }

        // Write a real record immediately (as the app SP) so the list shows a persisted row and
        // a first lifecycle event during the deploy job's serverless cold-start (~30s) — no
        // client-only placeholder / "waiting" gap. The job then upserts this same row (keyed on
        // deployment_id) as it runs; its retry-reset later replaces this 'submitted' event with
        // the wrapper→validator→deployer events. Best-effort: if it fails, the job still creates
        // the row after cold-start (the optimistic row bridges the gap in that case).
        try {
          const uc = spec.uc;
          const ucFull = `${uc.catalog}.${uc.schema}.${uc.model}`;
          await pgQuery(
            `INSERT INTO ${PG_SCHEMA}.model_deployments
               (deployment_id, model_name, description, uc_catalog, uc_schema, uc_model,
                uc_full_name, experiment_name, eval_dataset, serverless_usage_policy, tags,
                compute_type, gpu_type, compute_size, scale_to_zero, artifacts_json,
                input_schema_json, output_schema_json, permissions_json, status, stage,
                deployed_by, deployed_date, updated_at)
             VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,
                     'IN_PROGRESS','submitted',$20, now(), now())
             ON CONFLICT (deployment_id) DO NOTHING`,
            [
              deploymentId, spec.name, spec.description, uc.catalog, uc.schema, uc.model,
              ucFull, spec.experiment_name, spec.eval_dataset, spec.serverless_usage_policy,
              JSON.stringify(spec.tags ?? {}), spec.compute.compute_type,
              spec.compute.gpu_type ?? null, spec.compute.size, spec.compute.scale_to_zero,
              JSON.stringify(spec.artifacts ?? []), JSON.stringify(spec.input_schema ?? []),
              JSON.stringify(spec.output_schema ?? []), JSON.stringify(spec.permissions ?? {}),
              email,
            ],
          );
          await pgQuery(
            `INSERT INTO ${PG_SCHEMA}.model_lifecycle_events
               (event_id, deployment_id, model_name, uc_full_name, model_version, stage, status,
                message, actor, event_time)
             VALUES ($1,$2,$3,$4,'','submitted','IN_PROGRESS','deployment request submitted',$5, now())
             ON CONFLICT (event_id) DO NOTHING`,
            [Date.now(), deploymentId, spec.name, ucFull, email],
          );
        } catch (e) {
          console.error('[lakebase] initial record write failed (job will create it):', e);
        }

        res.status(202).json({
          ok: true,
          deployment_id: String(deploymentId),
          run: result.data,
        });
      });
    });
  },
}).catch(console.error);
