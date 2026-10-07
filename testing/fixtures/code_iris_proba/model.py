"""Code-folder fixture: iris classifier returning several output columns.

Folder layout:
  model.py           this file
  requirements.txt
  iris_rf.pkl        RandomForestClassifier (4 features -> class 0/1/2) -> context.artifacts["iris_rf.pkl"]
  class_names.json   ["setosa", "versicolor", "virginica"]             -> context.artifacts["class_names.json"]

Contract: Schema — sepal_length, sepal_width, petal_length, petal_width (double)
                   -> prediction (long), class_name (string), confidence (double)
"""
import mlflow
from mlflow.pyfunc import PythonModel

FEATURES = ["sepal_length", "sepal_width", "petal_length", "petal_width"]


class IrisWithProba(PythonModel):
    def load_context(self, context):
        import json
        import joblib
        self.model = joblib.load(context.artifacts["iris_rf.pkl"])
        with open(context.artifacts["class_names.json"]) as f:
            self.names = json.load(f)

    def predict(self, context, model_input, params=None):
        import pandas as pd
        X = pd.DataFrame(model_input)[FEATURES].astype(float).to_numpy()
        proba = self.model.predict_proba(X)
        cls = proba.argmax(axis=1)
        return pd.DataFrame({
            "prediction": cls.astype("int64"),
            "class_name": [self.names[i] for i in cls],
            "confidence": proba.max(axis=1).astype(float),
        })


mlflow.models.set_model(IrisWithProba())
