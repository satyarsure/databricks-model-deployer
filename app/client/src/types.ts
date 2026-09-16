// One row of the model_deployments table (matches config/queries/deployments.sql).
// All columns come back as string | null from the analytics query.
export interface DeploymentRow {
  deployment_id: string;
  model_name: string | null;
  description: string | null;
  uc_full_name: string | null;
  uc_catalog: string | null;
  uc_schema: string | null;
  uc_model: string | null;
  model_version: string | null;
  status: string | null;
  stage: string | null;
  error_message: string | null;
  endpoint_name: string | null;
  invoke_url: string | null;
  experiment_name: string | null;
  eval_dataset: string | null;
  serverless_usage_policy: string | null;
  tags: string | null;
  artifacts_json: string | null;
  input_schema_json: string | null;
  output_schema_json: string | null;
  permissions_json: string | null;
  compute_type: string | null;
  gpu_type: string | null;
  compute_size: string | null;
  scale_to_zero: string | null;
  deployed_by: string | null;
  deployed_date: string | null;
  updated_at: string | null;
}

// A deployment the user just submitted, shown optimistically on the Deployed Models
// tab until the (serverless, cold-starting) deploy job writes its first real row.
export interface PendingDeployment {
  deployment_id: string;
  model_name: string;
  uc_full_name: string;
}
