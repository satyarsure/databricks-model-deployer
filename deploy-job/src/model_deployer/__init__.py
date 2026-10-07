"""Shared code for the Model Deployer deploy job.

The job is a DAG of thin task notebooks (src/notebooks/NN_*.py); each one builds a `Ctx` from its
widgets and calls into these modules:

  context      — job parameters, the deploy spec, the plan handed between tasks, Lakebase writers
  sources      — where an artifact lives (UC Volume / S3 -> its UC external volume)
  formats      — what an artifact is (model file / MLflow model folder / code folder) + registration
  contract     — model signature + input examples from the form's schema or sample
  serving      — serving endpoint create/update, tags, AI Gateway, permissions, smoke test, rollback
  file_model   — the MLflow models-from-code wrapper used for single model files (logged as code,
                 so it is never cloudpickled)
"""
