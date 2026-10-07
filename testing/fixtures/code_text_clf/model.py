"""Code-folder fixture: text classifier + a label-mapping artifact.

Folder layout (every entry except model.py / requirements.txt is passed to load_context):
  model.py                 this file
  requirements.txt
  protocol_text_clf.pkl    sklearn text Pipeline (TF-IDF + classifier) -> context.artifacts["protocol_text_clf.pkl"]
  labels.json              raw label -> display label           -> context.artifacts["labels.json"]

Contract: Sample — {"instances": ["Pregnancy Test", ...]} -> {"predictions": ["Non-invasive procedure", ...]}
"""
import mlflow
from mlflow.pyfunc import PythonModel


class ProcedureLabelModel(PythonModel):
    def load_context(self, context):
        import json
        import joblib
        self.clf = joblib.load(context.artifacts["protocol_text_clf.pkl"])
        with open(context.artifacts["labels.json"]) as f:
            self.labels = json.load(f)

    def predict(self, context, model_input, params=None):
        import pandas as pd
        # Accept a list of strings ({"instances": [...]}) or a one-column DataFrame.
        texts = model_input.iloc[:, 0].astype(str).tolist() if isinstance(model_input, pd.DataFrame) \
            else [str(t) for t in model_input]
        return [self.labels.get(str(p), str(p)) for p in self.clf.predict(texts)]


mlflow.models.set_model(ProcedureLabelModel())
