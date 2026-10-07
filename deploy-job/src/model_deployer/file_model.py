"""MLflow models-from-code wrapper for a single model FILE (pickle / joblib).

Logged with `mlflow.pyfunc.log_model(python_model=<this file>, artifacts={"model": <file>})`, so
the model file is copied into the MLflow model untouched and only unpickled in `load_context` at
serving time — the wrapper itself is code, never cloudpickled (no notebook globals such as the
IPython `open` leak into the served model). Must stay self-contained: serving imports only this file.
"""
import mlflow
from mlflow.pyfunc import PythonModel


def load_model_file(path):
    try:
        import joblib
        return joblib.load(path)
    except Exception:
        import pickle
        with open(path, "rb") as f:
            return pickle.load(f)


class FileModel(PythonModel):
    """Generic pyfunc around an external model object (Genesis Workbench GWBModel style)."""

    def load_context(self, context):
        self.model = load_model_file(context.artifacts["model"])

    def predict(self, context, model_input, params=None):
        import numpy as np
        import pandas as pd
        data = model_input
        if isinstance(data, pd.DataFrame):
            # A single string/object column (e.g. a text classifier) must be handed to the model as
            # a 1-D sequence of strings: sklearn text vectorizers iterate a DataFrame over COLUMN
            # NAMES, silently collapsing every row into ONE prediction. Numeric or multi-column
            # frames are left as 2-D (tabular models expect that).
            if data.shape[1] == 1 and data.dtypes.iloc[0] == object:
                data = data.iloc[:, 0].tolist()
        elif isinstance(data, dict):
            data = pd.DataFrame([data])
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            data = pd.DataFrame(data)   # list of records -> DataFrame
        # np.ndarray, or a plain list of scalars/strings (e.g. {"instances": [...]}), passes through.
        preds = self.model.predict(data)
        # Return 1-D predictions FLAT so serving responds {"predictions": [v, v, ...]} — matching a
        # {"instances": [...]} contract — instead of nested records [{"0": v}, ...]. Multi-column
        # (2-D) outputs are returned as a DataFrame so each output field keeps its column.
        arr = np.asarray(preds)
        return arr.tolist() if arr.ndim <= 1 else pd.DataFrame(preds)


mlflow.models.set_model(FileModel())
