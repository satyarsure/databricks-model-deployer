"""Code-folder fixture: weighted ensemble of two churn models, with a helper module.

Folder layout:
  model.py                  this file
  requirements.txt
  code/churn_features.py    helper module (logged as code_paths; importable at load time)
  models/                   a FOLDER artifact -> context.artifacts["models"] (a directory path)
    churn_logreg.pkl
    churn_rf.pkl
  ensemble.json             blend weights + decision threshold -> context.artifacts["ensemble.json"]

Contract: Schema — tenure, monthly_charges, total_charges (double) -> churn (long)
"""
import mlflow
from mlflow.pyfunc import PythonModel


class ChurnEnsemble(PythonModel):
    def load_context(self, context):
        import json
        import os
        import joblib
        with open(context.artifacts["ensemble.json"]) as f:
            self.cfg = json.load(f)
        mdir = context.artifacts["models"]
        self.models = {name: joblib.load(os.path.join(mdir, name)) for name in self.cfg["weights"]}

    def predict(self, context, model_input, params=None):
        # Imported here (not at module top): the helper is on sys.path via code_paths when the
        # model is loaded, and the deploy job imports this file when logging it.
        from churn_features import blend, to_matrix
        X = to_matrix(model_input)
        probs = {name: m.predict_proba(X)[:, 1] for name, m in self.models.items()}
        return blend(probs, self.cfg["weights"], self.cfg["threshold"]).tolist()


mlflow.models.set_model(ChurnEnsemble())
