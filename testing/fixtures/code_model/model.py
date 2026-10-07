"""Code-folder fixture: a PythonModel whose artifacts are the other files in this folder.

Deployed as a "Code folder" variant: the deploy job logs it with MLflow models-from-code and passes
every other entry of the folder as context.artifacts[<file name>]. Upload this folder together with
house_price_linreg.pkl (from setup_test_artifacts.py) and requirements.txt.
"""
import mlflow
from mlflow.pyfunc import PythonModel


class HousePriceModel(PythonModel):
    def load_context(self, context):
        import joblib  # heavy imports inside load_context: the job imports this file at log time
        self.model = joblib.load(context.artifacts["house_price_linreg.pkl"])

    def predict(self, context, model_input, params=None):
        import numpy as np
        return np.asarray(self.model.predict(model_input)).tolist()


mlflow.models.set_model(HousePriceModel())
