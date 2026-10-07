"""Helper module for the churn ensemble fixture (shipped via code_paths)."""
import numpy as np
import pandas as pd

FEATURES = ["tenure", "monthly_charges", "total_charges"]


def to_matrix(df):
    """Column-ordered float matrix (the models were trained on arrays in FEATURES order)."""
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df, columns=FEATURES)
    return df[FEATURES].astype(float).to_numpy()


def blend(probs, weights, threshold):
    """Weighted average of each model's churn probability -> 0/1 label."""
    total = sum(weights.values())
    p = sum(np.asarray(probs[name]) * w for name, w in weights.items()) / total
    return (p >= threshold).astype(int)
