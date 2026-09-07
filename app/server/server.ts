import { createApp, analytics, jobs, server } from '@databricks/appkit';
import { z } from 'zod';

// ---- Deploy spec validation -------------------------------------------------
const fieldSchema = z.object({
  name: z.string().min(1),
  type: z.string().min(1),
});

const artifactSchema = z.object({
  label: z.string().optional(),
  type: z.enum(['s3', 'uc_volume']),
  path: z.string().min(1),
  traffic_percent: z.number().min(0).max(100).optional(),
});

const deploySpecSchema = z.object({
  name: z.string().min(1),
  description: z.string().optional().default(''),
  artifacts: z.array(artifactSchema).min(1),
  input_schema: z.array(fieldSchema).default([]),
  output_schema: z.array(fieldSchema).min(1),
  experiment_name: z.string().min(1),
  eval_dataset: z.string().optional().default(''),
  serverless_usage_policy: z.string().min(1),
  tags: z.record(z.string(), z.any()).default({}),
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
  // This is a live deployment-status board: the deployments list and lifecycle timeline
  // must always reflect current state. AppKit's analytics query cache is on by default and
  // can serve a stale list (e.g. hiding an in-progress deployment after a page reload), so
  // disable it — the queries are small and run on the warehouse each time.
  cache: { enabled: false },
  plugins: [
    analytics(),
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
  ],
  async onPluginsReady(appkit) {
    appkit.server.extend((app) => {
      // Resolve the deployments table (catalog.schema) from the bound deploy job's
      // notebook parameters, so nothing deployment-specific is hardcoded in the app.
      // Falls back to env vars, then to main.default.
      interface JobBaseParams {
        catalog?: string;
        schema?: string;
      }
      interface JobTask {
        notebook_task?: { base_parameters?: JobBaseParams };
      }
      interface JobLike {
        settings?: { tasks?: JobTask[] };
        tasks?: JobTask[];
      }
      app.get('/api/config', async (_req, res) => {
        let catalog = process.env.DEPLOYMENTS_CATALOG || 'main';
        let schema = process.env.DEPLOYMENTS_SCHEMA || 'default';
        try {
          const result = await appkit.jobs('default').getJob();
          if (result.ok) {
            const data = result.data as JobLike;
            const tasks = data.settings?.tasks ?? data.tasks ?? [];
            const bp = tasks[0]?.notebook_task?.base_parameters ?? {};
            if (bp.catalog) catalog = bp.catalog;
            if (bp.schema) schema = bp.schema;
          }
        } catch {
          // fall back to env / defaults
        }
        res.json({
          catalog,
          schema,
          deploymentsTable: `${catalog}.${schema}.model_deployments`,
          lifecycleTable: `${catalog}.${schema}.model_lifecycle_events`,
        });
      });

      // Signed-in user (for header display).
      app.get('/api/whoami', (req, res) => {
        const email =
          (req.headers['x-forwarded-email'] as string) ||
          (req.headers['x-forwarded-user'] as string) ||
          '';
        res.json({ email });
      });

      // Kick off a deployment: validate the spec and trigger the deploy Job.
      // The Job writes all status rows to the model_deployments Delta table,
      // which the UI reads via the analytics query.
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
        res.status(202).json({
          ok: true,
          deployment_id: String(deploymentId),
          run: result.data,
        });
      });
    });
  },
}).catch(console.error);
