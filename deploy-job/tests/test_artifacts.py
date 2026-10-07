"""Unit tests for model_deployer.artifacts — pure logic, no Databricks needed.

Run from deploy-job/:  pip install -r requirements.txt pytest && pytest tests
"""
import json
import pickle

import pandas as pd
import pytest
import yaml

from model_deployer import artifacts


class FakeSpark:
    """Stands in for spark.sql(...) over system.information_schema.volumes."""

    def __init__(self, volumes):
        self.volumes = volumes  # [(catalog, schema, volume, storage_location)]

    def sql(self, _query, args):
        p = args["p"]
        rows = [dict(volume_catalog=c, volume_schema=s, volume_name=v, loc=loc.rstrip("/"))
                for c, s, v, loc in self.volumes
                if p == loc.rstrip("/") or p.startswith(loc.rstrip("/") + "/")]
        rows.sort(key=lambda r: -len(r["loc"]))

        class _Result:
            def collect(self):
                return rows[:1]
        return _Result()


SPARK = FakeSpark([("cat", "sch", "models", "s3://bucket/models"),
                   ("cat", "sch", "team", "s3://bucket/models/team/")])


# ---- 1. Location ---------------------------------------------------------------------------
def test_uc_volume_path_is_read_as_is():
    assert artifacts.resolve_path(SPARK, "uc_volume", "/Volumes/a/b/c/m.pkl") == "/Volumes/a/b/c/m.pkl"


@pytest.mark.parametrize("s3_path, expected", [
    ("s3://bucket/models/m.pkl", "/Volumes/cat/sch/models/m.pkl"),
    ("s3a://bucket/models/m.pkl", "/Volumes/cat/sch/models/m.pkl"),       # s3a / s3n accepted
    ("S3://bucket/models/x/", "/Volumes/cat/sch/models/x"),               # trailing slash dropped
    ("s3://bucket/models/team/m.pkl", "/Volumes/cat/sch/team/m.pkl"),     # longest prefix wins
    ("s3://bucket/models", "/Volumes/cat/sch/models"),                    # the volume root itself
])
def test_s3_path_is_read_through_its_external_volume(s3_path, expected):
    assert artifacts.resolve_path(SPARK, "s3", s3_path) == expected


def test_s3_path_without_a_volume_fails_clearly():
    with pytest.raises(ValueError, match="No Unity Catalog external volume covers"):
        artifacts.resolve_path(SPARK, "s3", "s3://bucket/modelsX/m.pkl")   # not a real prefix match


def test_unknown_location_type_fails():
    with pytest.raises(ValueError, match="Unsupported location type"):
        artifacts.resolve_path(SPARK, "gcs", "gs://b/m.pkl")


@pytest.mark.parametrize("spec, expected", [
    ({}, ""),
    ({"eval_dataset": "/Volumes/a/b/c/e.csv"}, "uc_volume"),
    ({"eval_dataset": "s3://b/e.csv"}, "s3"),
    ({"eval_dataset": "s3://b/e.csv", "eval_dataset_type": "uc_volume"}, "s3"),  # s3:// always wins
])
def test_eval_dataset_location(spec, expected):
    assert artifacts.eval_dataset_location(spec) == expected


# ---- 2. Format -----------------------------------------------------------------------------
def test_detect_format(tmp_path):
    (tmp_path / "m.pkl").write_bytes(b"x")
    (tmp_path / "mlflow_dir").mkdir()
    (tmp_path / "mlflow_dir" / "MLmodel").write_text("flavors: {}")
    (tmp_path / "code_dir").mkdir()
    (tmp_path / "code_dir" / "model.py").write_text("")
    assert artifacts.detect_format(str(tmp_path / "m.pkl")) == "file"
    assert artifacts.detect_format(str(tmp_path / "mlflow_dir")) == "mlflow_model"
    assert artifacts.detect_format(str(tmp_path / "code_dir")) == "code_folder"


def test_detect_format_rejects_unknown_folder_and_missing_path(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="neither an MLmodel file"):
        artifacts.detect_format(str(tmp_path / "empty"))
    with pytest.raises(FileNotFoundError):
        artifacts.detect_format(str(tmp_path / "nope"))


# ---- 3. Contract ---------------------------------------------------------------------------
SCHEMA_SPEC = {"input_schema": [{"name": "x", "type": "double"}, {"name": "n", "type": "int"}],
               "output_schema": [{"name": "y", "type": "long"}]}
SAMPLE_SPEC = {"sample_input": json.dumps({"instances": ["a", "b"]}),
               "sample_output": json.dumps({"predictions": ["p", "q"]})}


def test_contract_mode():
    assert artifacts.contract_mode(SCHEMA_SPEC) == "schema"
    assert artifacts.contract_mode(SAMPLE_SPEC) == "sample"
    assert artifacts.contract_mode({"contract_mode": "model"}) == "model"


def test_schema_signature_and_example():
    sig = artifacts.schema_signature(SCHEMA_SPEC)
    assert [c.name for c in sig.inputs.inputs] == ["x", "n"]
    assert sig.outputs.inputs[0].type.name == "long"
    ex = artifacts.schema_example(SCHEMA_SPEC)
    assert list(ex.columns) == ["x", "n"] and len(ex) == 1


def test_sample_helpers():
    assert artifacts.sample_input(SAMPLE_SPEC) == ["a", "b"]
    assert artifacts.sample_output(SAMPLE_SPEC) == ["p", "q"]
    assert artifacts.sample_request(SAMPLE_SPEC) == {"instances": ["a", "b"]}
    assert artifacts.sample_request({"sample_input": '["a"]'}) == {"inputs": ["a"]}
    records = artifacts.sample_input({"sample_input": json.dumps({"dataframe_records": [{"x": 1}]})})
    assert isinstance(records, pd.DataFrame) and list(records.columns) == ["x"]


def test_form_signature():
    assert artifacts.form_signature(SCHEMA_SPEC) is not None
    assert artifacts.form_signature(SAMPLE_SPEC) is not None
    assert artifacts.form_signature({"contract_mode": "model"}) is None


# ---- 4. Register helpers -------------------------------------------------------------------
def test_add_requirement_is_idempotent(tmp_path):
    (tmp_path / "requirements.txt").write_text("mlflow==3.16.1\ntorch\n")
    (tmp_path / "conda.yaml").write_text(yaml.safe_dump(
        {"dependencies": ["python=3.12", {"pip": ["mlflow==3.16.1"]}]}))
    for _ in range(2):
        artifacts._add_requirement(str(tmp_path), "ipython")
    assert (tmp_path / "requirements.txt").read_text().splitlines().count("ipython") == 1
    pip = yaml.safe_load((tmp_path / "conda.yaml").read_text())["dependencies"][1]["pip"]
    assert pip.count("ipython") == 1


def test_pickle_mentions(tmp_path):
    p = tmp_path / "m.pkl"
    p.write_bytes(pickle.dumps({"module": "IPython.core.interactiveshell"}))
    assert artifacts._pickle_mentions(str(p), "IPython")
    assert not artifacts._pickle_mentions(str(p), "torch")
