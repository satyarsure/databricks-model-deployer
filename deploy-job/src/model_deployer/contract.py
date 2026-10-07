"""The model contract from the Deploy form: a columnar SCHEMA (input_schema/output_schema -> a
ColSpec signature) or a real SAMPLE input + output (JSON; a list/array sample yields a TENSOR
signature, which serves the {"instances": [...]} contract). Unity Catalog requires every registered
model to have a signature; an MLflow model folder may bring its own (contract_mode "model").
"""
import json

import pandas as pd
from mlflow.models.signature import ModelSignature
from mlflow.types.schema import ColSpec, Schema

TYPE_MAP = {"double": "double", "float": "float", "int": "integer", "integer": "integer",
            "long": "long", "bigint": "long", "string": "string", "str": "string", "text": "string",
            "bool": "boolean", "boolean": "boolean", "datetime": "datetime", "binary": "binary"}
DUMMY = {"double": 0.0, "float": 0.0, "integer": 0, "long": 0, "string": "example",
         "boolean": False, "datetime": pd.Timestamp("2020-01-01"), "binary": b"0"}


def mlflow_type(t):
    return TYPE_MAP.get(str(t).lower(), "double")


def build_signature(inp, outp):
    inputs = Schema([ColSpec(mlflow_type(f["type"]), f["name"]) for f in inp]) if inp else None
    outputs = Schema([ColSpec(mlflow_type(f["type"]), f["name"]) for f in outp]) if outp else None
    return ModelSignature(inputs=inputs, outputs=outputs) if inputs is not None and outputs is not None else None


def build_input_example(inp):
    return pd.DataFrame([{f["name"]: DUMMY.get(mlflow_type(f["type"]), 0.0) for f in inp}]) if inp else None


def parse_json_maybe(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    s = str(v).strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def unwrap_input(o):
    """Users paste serving envelopes ({"instances"/"inputs": ...}, dataframe_records/split); unwrap."""
    if isinstance(o, dict):
        for k in ("instances", "inputs"):
            if k in o:
                return o[k]
        if "dataframe_records" in o:
            return pd.DataFrame(o["dataframe_records"])
        if "dataframe_split" in o:
            ds = o["dataframe_split"]
            return pd.DataFrame(ds.get("data", []), columns=ds.get("columns"))
    return o


def unwrap_output(o):
    if isinstance(o, dict) and "predictions" in o:
        return o["predictions"]
    return o


def contract_mode(spec):
    """'model' (use the MLflow model's own signature), 'sample', or 'schema'."""
    m = (spec.get("contract_mode") or "").lower()
    if m in ("model", "sample", "schema"):
        return m
    has_sample = str(spec.get("sample_input") or "").strip() and str(spec.get("sample_output") or "").strip()
    return "sample" if has_sample else "schema"


class Contract:
    """Signature + examples derived from the form. `signature` is None when the form supplies none
    (contract_mode 'model', or a sample whose signature is inferred per variant after predicting)."""

    def __init__(self, spec):
        self.mode = contract_mode(spec)
        self.input_schema = spec.get("input_schema", []) or []
        self.output_schema = spec.get("output_schema", []) or []
        raw_in = parse_json_maybe(spec.get("sample_input"))
        raw_out = parse_json_maybe(spec.get("sample_output"))
        # The raw serving payload (e.g. {"instances": [...]}) is what the smoke test POSTs.
        self.sample_payload = raw_in if isinstance(raw_in, dict) else (
            {"inputs": raw_in} if raw_in is not None else None)
        self.sample_input = unwrap_input(raw_in) if raw_in is not None else None
        self.sample_output = unwrap_output(raw_out) if raw_out is not None else None
        self.signature = build_signature(self.input_schema, self.output_schema) if self.mode == "schema" else None
        self.input_example = (self.sample_input if self.sample_input is not None
                              else build_input_example(self.input_schema))

    @property
    def has_form_signature(self):
        """Can the form's contract produce a signature (schema, or sample input+output)?"""
        return self.signature is not None or (self.sample_input is not None and self.sample_output is not None)
