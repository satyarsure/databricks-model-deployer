"""Shared code for the Model Deployer deploy job.

The job runs four task notebooks in a straight line (src/notebooks/): 1_prepare -> 2_register ->
3_validate -> 4_deploy, plus 9_on_failure. Each starts with `%run ./_setup`, which builds the `ctx`
(see context.Ctx), then calls into:

  context     Ctx: job parameters, the deploy spec, the plan handed between tasks, Lakebase writes
  artifacts   where an artifact lives (UC Volume / S3), what it is (file / MLflow model folder /
              code folder), its contract (signature), and how it is registered in Unity Catalog
  serving     serving endpoint create/update, tags, AI Gateway, permissions, endpoint test +
              rollback, @champion
  file_model  the MLflow models-from-code wrapper for single model files (its own file because MLflow
              logs it as code)
"""
