# Databricks notebook source
# MAGIC %md
# MAGIC # Manual-testing fixtures for the Model Deployer
# MAGIC Trains a handful of small scikit-learn models and writes them (plus two eval
# MAGIC datasets) to a UC Volume so the Deploy Model form has real artifacts to point at.
# MAGIC
# MAGIC All artifacts land under `/<catalog>/<schema>/<volume>/test_models/`.
# MAGIC Nothing here is environment-specific — pass your catalog/schema/volume as widgets.

# COMMAND ----------
# Pin the SAME versions the deploy job / serving container uses (deploy-job/requirements.txt)
# so these fixture pickles load without a version-mismatch warning downstream.
# MAGIC %pip install --quiet scikit-learn==1.4.2 joblib==1.4.2 pandas==2.2.2 numpy==1.26.4
# MAGIC dbutils.library.restartPython()

# COMMAND ----------
dbutils.widgets.text("catalog", "main")
dbutils.widgets.text("schema", "default")
dbutils.widgets.text("volume", "artifacts")

CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
VOLUME = dbutils.widgets.get("volume")

BASE = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}/test_models"
import os
os.makedirs(BASE, exist_ok=True)
print("Writing fixtures to:", BASE)

# COMMAND ----------
import numpy as np, pandas as pd, joblib
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.ensemble import (
    GradientBoostingRegressor, RandomForestClassifier, GradientBoostingClassifier,
)

rng = np.random.default_rng(42)
saved = []

def save(model, name):
    path = f"{BASE}/{name}"
    joblib.dump(model, path)
    saved.append(path)
    print("  saved", path)

# COMMAND ----------
# 1) House-price LINEAR REGRESSION  ── features: sqft, bedrooms, bathrooms, age → price
n = 800
sqft = rng.uniform(600, 4000, n)
bedrooms = rng.integers(1, 6, n).astype(float)
bathrooms = rng.integers(1, 4, n).astype(float)
age = rng.uniform(0, 60, n)
price = (150 * sqft + 10000 * bedrooms + 15000 * bathrooms - 1000 * age
         + rng.normal(0, 8000, n))
Xhp = np.column_stack([sqft, bedrooms, bathrooms, age])
save(LinearRegression().fit(Xhp, price), "house_price_linreg.pkl")

# A v2 of the same model (slightly more data / different seed) for the "new version" test.
n2 = 1200
sqft2 = rng.uniform(600, 4000, n2); bd2 = rng.integers(1, 6, n2).astype(float)
ba2 = rng.integers(1, 4, n2).astype(float); ag2 = rng.uniform(0, 60, n2)
pr2 = 150*sqft2 + 10000*bd2 + 15000*ba2 - 1000*ag2 + rng.normal(0, 6000, n2)
save(LinearRegression().fit(np.column_stack([sqft2, bd2, ba2, ag2]), pr2),
     "house_price_linreg_v2.pkl")

# House-price eval dataset (target column name MUST equal the output-schema field → triggers mlflow.evaluate)
pd.DataFrame({
    "sqft": sqft[:100], "bedrooms": bedrooms[:100],
    "bathrooms": bathrooms[:100], "age": age[:100], "price": price[:100],
}).to_csv(f"{BASE}/house_price_eval.csv", index=False)
print("  saved", f"{BASE}/house_price_eval.csv")

# COMMAND ----------
# 2) Energy GRADIENT-BOOSTING REGRESSOR ── features: temperature, humidity, occupancy → energy_kwh
temperature = rng.uniform(15, 35, n)
humidity = rng.uniform(20, 90, n)
occupancy = rng.integers(0, 50, n).astype(float)
energy = (2.5 * temperature + 0.4 * humidity + 1.8 * occupancy
          + rng.normal(0, 3, n))
Xen = np.column_stack([temperature, humidity, occupancy])
save(GradientBoostingRegressor(random_state=0).fit(Xen, energy), "energy_gbr.pkl")
pd.DataFrame({
    "temperature": temperature[:100], "humidity": humidity[:100],
    "occupancy": occupancy[:100], "energy_kwh": energy[:100],
}).to_csv(f"{BASE}/energy_eval.csv", index=False)
print("  saved", f"{BASE}/energy_eval.csv")

# COMMAND ----------
# 3) Iris RANDOM-FOREST CLASSIFIER ── 4 sepal/petal features → class 0/1/2
from sklearn.datasets import load_iris
iris = load_iris()
save(RandomForestClassifier(n_estimators=50, random_state=0).fit(iris.data, iris.target),
     "iris_rf.pkl")

# COMMAND ----------
# 4) Churn A/B pair ── features: tenure, monthly_charges, total_charges → churn 0/1
tenure = rng.uniform(0, 72, n)
monthly = rng.uniform(20, 120, n)
total = tenure * monthly + rng.normal(0, 50, n)
# higher monthly + lower tenure → more likely to churn
churn = ((monthly / 120 - tenure / 72) + rng.normal(0, 0.15, n) > 0).astype(int)
Xch = np.column_stack([tenure, monthly, total])
save(LogisticRegression(max_iter=1000).fit(Xch, churn), "churn_logreg.pkl")
save(RandomForestClassifier(n_estimators=60, random_state=0).fit(Xch, churn), "churn_rf.pkl")

# COMMAND ----------
# 5) Credit-risk GRADIENT-BOOSTING CLASSIFIER (multiclass) ── income, age, loan_amount, credit_score → 0/1/2
income = rng.uniform(20000, 200000, n)
cage = rng.uniform(21, 70, n)
loan = rng.uniform(1000, 100000, n)
score = rng.uniform(300, 850, n)
# crude risk buckets from a score-ish combination
raw = (score / 850) - (loan / 100000) + (income / 200000) - (cage / 200)
risk = np.digitize(raw, bins=[0.4, 0.8])  # → 0 (high), 1 (medium), 2 (low)
Xcr = np.column_stack([income, cage, loan, score])
save(GradientBoostingClassifier(random_state=0).fit(Xcr, risk), "credit_risk_gbc.pkl")

# COMMAND ----------
# 6) A non-model file for the "wrapper fails to load an artifact" negative test.
with open(f"{BASE}/not_a_model.txt", "w") as f:
    f.write("this is not a pickled model")
print("  saved", f"{BASE}/not_a_model.txt")

# COMMAND ----------
print("\nDONE. Artifacts written:")
for p in saved:
    print(" ", p)
print("  " + f"{BASE}/house_price_eval.csv")
print("  " + f"{BASE}/energy_eval.csv")
print("  " + f"{BASE}/not_a_model.txt")
