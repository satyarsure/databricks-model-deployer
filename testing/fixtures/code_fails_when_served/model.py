"""TEST-ONLY fixture: predicts normally in the deploy job's validate step, but FAILS once served.

Used to test the deploy step's rollback (TC14): validation passes (the model's own environment
runs inside the job's compute, where DATABRICKS_RUNTIME_VERSION is set), then the live endpoint
test fails (Model Serving containers don't set it) -> the endpoint is rolled back to its previous
version. Deploy it as a NEW VERSION of a model that already has a working endpoint.

Folder: model.py, requirements.txt, house_price_linreg.pkl (from setup_test_artifacts.py).
"""
import mlflow
from mlflow.pyfunc import PythonModel


class FailsWhenServed(PythonModel):
    def load_context(self, context):
        import joblib
        self.model = joblib.load(context.artifacts["house_price_linreg.pkl"])

    def predict(self, context, model_input, params=None):
        import os
        import numpy as np
        if "DATABRICKS_RUNTIME_VERSION" not in os.environ:
            raise RuntimeError("test fixture: refusing to predict outside the deploy job (rollback test)")
        return np.asarray(self.model.predict(model_input)).tolist()


mlflow.models.set_model(FailsWhenServed())
