"""Evaluation helpers shared by the in-job and isolated validation tasks."""
import mlflow
import numpy as np
import pandas as pd


def load_eval_dataset(path):
    if not path:
        return None
    if path.endswith(".parquet"):
        return pd.read_parquet(path)
    if path.endswith(".csv"):
        return pd.read_csv(path)
    return None


def eval_target(spec, df):
    """(target column, model_type) from the first output-schema field, or (None, None) when the
    dataset has no matching target column (then rows are only scored). Integer-typed outputs are
    class labels -> classifier metrics; everything else -> regressor metrics."""
    out_fields = spec.get("output_schema", []) or []
    f = out_fields[0] if out_fields else None
    if not f or f["name"] not in df.columns:
        return None, None
    ftype = str(f.get("type", "")).lower()
    return f["name"], ("classifier" if ftype in ("long", "int", "integer", "bigint") else "regressor")


def evaluate_predictions(df, target, model_type, preds):
    """mlflow evaluate as a static dataset (predictions already computed). Single-output
    predictions are squeezed to 1-D so metrics don't hit a predictions/targets shape mismatch."""
    if isinstance(preds, pd.DataFrame):
        preds = preds[target] if target in preds.columns else preds.iloc[:, 0]
    elif len(preds) and isinstance(preds[0], dict):
        # Multi-output models return records ([{"<field>": v, ...}, ...]); score the target field.
        key = target if target in preds[0] else next(iter(preds[0]))
        preds = [r[key] for r in preds]
    edf = df.drop(columns=[target]).copy()
    edf[target] = df[target].values
    edf["prediction_"] = np.asarray(preds).squeeze()
    evaluate = getattr(mlflow.models, "evaluate", None) or mlflow.evaluate
    evaluate(data=edf, predictions="prediction_", targets=target, model_type=model_type)
