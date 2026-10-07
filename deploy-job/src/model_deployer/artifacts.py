"""Everything about the model a user hands in: where it lives, what it is, its contract (signature),
and how it becomes a Unity Catalog model version.

Sections (top to bottom):
  1. Location  — UC Volume paths are read as-is; S3 paths are read through the UC external volume
                 that covers them (resolve_path).
  2. Format    — what is at the path: a model file, an MLflow model folder, or a code folder
                 (detect_format).
  3. Contract  — the model signature from the form: a columnar schema, a sample input/output, or
                 (MLflow model folders) the model's own signature.
  4. Register  — turn one variant into a UC model version (register_variant).

To add a new LOCATION: add an `elif` in resolve_path() and the option in the app
(ARTIFACT_TYPES in app/server/server.ts + DeployModel.tsx).
To add a new FORMAT: add detection in detect_format(), an `elif` in register_variant(), and the
option in the app (ARTIFACT_FORMATS in server.ts + DeployModel.tsx).
"""
import json
import os
import pickletools
import re
import shutil
import tempfile

import mlflow
import pandas as pd
import yaml
from mlflow.models import Model, infer_signature
from mlflow.models.signature import ModelSignature
from mlflow.tracking import MlflowClient
from mlflow.types.schema import ColSpec, Schema

# =====================================================================================
# 1. Location
# =====================================================================================
S3_SCHEME = re.compile(r"^s3[an]?://", re.IGNORECASE)


def resolve_path(spark, location_type, path):
    """The /Volumes path to read for an artifact or eval dataset at `path` of `location_type`."""
    t = (location_type or "").lower()
    if t == "uc_volume":
        return path
    elif t == "s3":
        return s3_to_volume_path(spark, path)
    raise ValueError(f"Unsupported location type {location_type!r} for {path} (supported: uc_volume, s3)")


def s3_to_volume_path(spark, s3_path):
    """Every S3 location is registered in UC as an EXTERNAL VOLUME, so an S3 path is read through
    that volume (UC-governed, no AWS credentials on the job). The covering volume is looked up in
    system.information_schema.volumes (longest storage_location prefix wins) and the path is
    rewritten to /Volumes/<catalog>/<schema>/<volume>/<rest>. information_schema only lists volumes
    the job's run-as identity has a privilege on, so it needs READ VOLUME on the volume."""
    p = S3_SCHEME.sub("s3://", str(s3_path).strip()).rstrip("/")
    rows = spark.sql(
        """WITH v AS (
             SELECT volume_catalog, volume_schema, volume_name,
                    regexp_replace(regexp_replace(storage_location, '^[sS]3[aAnN]?://', 's3://'),
                                   '/+$', '') AS loc
             FROM system.information_schema.volumes
             WHERE volume_type = 'EXTERNAL' AND storage_location IS NOT NULL)
           SELECT * FROM v
           WHERE :p = loc OR startswith(:p, concat(loc, '/'))
           ORDER BY length(loc) DESC LIMIT 1""",
        args={"p": p},
    ).collect()
    if not rows:
        raise ValueError(
            f"No Unity Catalog external volume covers {s3_path} (or the job's identity has no access "
            f"to it). Register the S3 location as an external volume and grant READ VOLUME on it.")
    r = rows[0]
    rest = p[len(r["loc"]):].lstrip("/")
    return "/".join(["/Volumes", r["volume_catalog"], r["volume_schema"], r["volume_name"]]
                    + ([rest] if rest else []))


def eval_dataset_location(spec):
    """The eval dataset's location type: the form's choice, but an s3:// path is always treated as
    S3 (so it can't be read unresolved); an untyped path is classified by its shape. "" = none."""
    ev = (spec.get("eval_dataset") or "").strip()
    if not ev:
        return ""
    if S3_SCHEME.match(ev):
        return "s3"
    return (spec.get("eval_dataset_type") or "uc_volume").lower()


# =====================================================================================
# 2. Format
# =====================================================================================
FORMATS = ("file", "mlflow_model", "code_folder")
# Never copied into a registered model (OS / editor / notebook noise).
JUNK = (".DS_Store", "._*", "__MACOSX", ".ipynb_checkpoints", "Thumbs.db")


def detect_format(path):
    """What is actually at `path`: 'file' | 'mlflow_model' | 'code_folder'."""
    if os.path.isdir(path):
        if os.path.exists(os.path.join(path, "MLmodel")):
            return "mlflow_model"
        if os.path.exists(os.path.join(path, "model.py")):
            return "code_folder"
        raise ValueError(f"{path} is a folder but has neither an MLmodel file (MLflow model folder) "
                         f"nor a model.py (code folder)")
    if os.path.isfile(path):
        return "file"
    raise FileNotFoundError(f"artifact not found: {path}")


def mlflow_folder_has_signature(path):
    """Read an MLflow model folder's MLmodel (without loading the model — its dependencies may not
    be installed here): it must be a pyfunc; returns whether it carries a signature."""
    m = Model.load(path)
    if "python_function" not in (m.flavors or {}):
        raise ValueError(f"{path}: MLflow model has no python_function flavor (flavors: {sorted(m.flavors or {})})")
    return m.signature is not None and m.signature.inputs is not None


# =====================================================================================
# 3. Contract (signature + examples from the form)
# =====================================================================================
TYPE_MAP = {"double": "double", "float": "float", "int": "integer", "integer": "integer",
            "long": "long", "bigint": "long", "string": "string", "str": "string", "text": "string",
            "bool": "boolean", "boolean": "boolean", "datetime": "datetime", "binary": "binary"}
DUMMY = {"double": 0.0, "float": 0.0, "integer": 0, "long": 0, "string": "example",
         "boolean": False, "datetime": pd.Timestamp("2020-01-01"), "binary": b"0"}


def contract_mode(spec):
    """'schema' (columnar input/output schema), 'sample' (sample input + output), or 'model' (the
    MLflow model folder's own signature)."""
    m = (spec.get("contract_mode") or "").lower()
    if m in ("schema", "sample", "model"):
        return m
    return "sample" if (_text(spec, "sample_input") and _text(spec, "sample_output")) else "schema"


def _text(spec, key):
    return str(spec.get(key) or "").strip()


def _json(spec, key):
    try:
        return json.loads(_text(spec, key)) if _text(spec, key) else None
    except Exception:
        return None


def sample_request(spec):
    """The form's sample input as a serving request body, e.g. {"instances": [...]} — or None."""
    raw = _json(spec, "sample_input")
    if raw is None:
        return None
    return raw if isinstance(raw, dict) else {"inputs": raw}


def sample_input(spec):
    """The form's sample input unwrapped from its serving envelope (what predict() receives)."""
    raw = _json(spec, "sample_input")
    if isinstance(raw, dict):
        for k in ("instances", "inputs"):
            if k in raw:
                return raw[k]
        if "dataframe_records" in raw:
            return pd.DataFrame(raw["dataframe_records"])
        if "dataframe_split" in raw:
            ds = raw["dataframe_split"]
            return pd.DataFrame(ds.get("data", []), columns=ds.get("columns"))
    return raw


def sample_output(spec):
    raw = _json(spec, "sample_output")
    return raw["predictions"] if isinstance(raw, dict) and "predictions" in raw else raw


def schema_signature(spec):
    """Signature from the columnar input/output schema (schema mode), else None."""
    inp, outp = spec.get("input_schema") or [], spec.get("output_schema") or []
    if not inp or not outp:
        return None
    return ModelSignature(
        inputs=Schema([ColSpec(TYPE_MAP.get(str(f["type"]).lower(), "double"), f["name"]) for f in inp]),
        outputs=Schema([ColSpec(TYPE_MAP.get(str(f["type"]).lower(), "double"), f["name"]) for f in outp]))


def schema_example(spec):
    """A one-row SYNTHETIC example built from the input schema (zeros / "example"), else None."""
    inp = spec.get("input_schema") or []
    if not inp:
        return None
    return pd.DataFrame([{f["name"]: DUMMY.get(TYPE_MAP.get(str(f["type"]).lower(), "double"), 0.0) for f in inp}])


def form_signature(spec):
    """The signature the form defines: the schema, or one inferred from the sample input + output.
    None when the form defines none (contract mode 'model')."""
    mode = contract_mode(spec)
    if mode == "schema":
        return schema_signature(spec)
    if mode == "sample" and sample_input(spec) is not None and sample_output(spec) is not None:
        return infer_signature(sample_input(spec), sample_output(spec))
    return None


# =====================================================================================
# 4. Register: one variant -> one UC model version
# =====================================================================================
FILE_MODEL_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "file_model.py")
# Files in a code folder that are NOT passed to the model as artifacts.
CODE_FOLDER_RESERVED = {"model.py", "requirements.txt", "code"}


def served_pip_requirements():
    """Requirements for a wrapped MODEL FILE: the exact versions in use in this job, so the serving
    container always matches the versions the file was loaded with."""
    import importlib.metadata as im
    reqs = [f"mlflow=={mlflow.__version__}"]
    for p in ["scikit-learn", "pandas", "numpy", "scipy", "cloudpickle", "joblib", "xgboost", "lightgbm"]:
        try:
            reqs.append(f"{p}=={im.version(p)}")
        except Exception:
            pass
    return reqs


def register_variant(ctx, art, label):
    """Register one new variant in UC. Returns (version, notes for the timeline)."""
    fmt = art["format"]
    if fmt == "file":
        return _register_file(ctx, art["path"], label)
    elif fmt == "mlflow_model":
        return _register_mlflow_folder(ctx, art["path"], label)
    elif fmt == "code_folder":
        return _register_code_folder(ctx, art["path"], label)
    raise ValueError(f"Unsupported artifact format {fmt!r} (supported: {', '.join(FORMATS)})")


def _register_file(ctx, path, label):
    """A single pickle/joblib model file, wrapped by FileModel (file_model.py). The file is logged
    as an artifact and loaded in load_context — the wrapper is code, never cloudpickled."""
    from model_deployer.file_model import FileModel, load_model_file
    spec = ctx.spec
    # Load it here first so a file that isn't a loadable model fails NOW, before anything is registered.
    model = FileModel()
    try:
        model.model = load_model_file(path)
    except Exception as e:
        raise ValueError(f"{path} is not a loadable pickle/joblib model file: {e}") from e
    if contract_mode(spec) == "sample":
        # Infer the signature from the real sample, preferring the model's ACTUAL output.
        try:
            out = model.predict(None, sample_input(spec))
        except Exception as e:
            print(f"[register] sample predict failed; using the provided sample output: {e}")
            out = sample_output(spec)
        signature, example = infer_signature(sample_input(spec), out), sample_input(spec)
    else:
        signature, example = schema_signature(spec), schema_example(spec)
    with mlflow.start_run(run_name=f"{spec.get('name')}_{label}"):
        info = mlflow.pyfunc.log_model(
            name="model", python_model=FILE_MODEL_PY, artifacts={"model": path},
            signature=signature, input_example=example, pip_requirements=served_pip_requirements(),
            registered_model_name=ctx.uc_full)
    return int(info.registered_model_version), []


def _register_mlflow_folder(ctx, path, label):
    """An exported MLflow model folder (possibly from ANOTHER workspace), registered AS-IS: its own
    python_model, artifacts, signature and environment are kept. Changes are made to a copy only."""
    notes = []
    staged = os.path.join(tempfile.mkdtemp(prefix="mlmodel_"), "model")
    shutil.copytree(path.rstrip("/"), staged, ignore=shutil.ignore_patterns(*JUNK))
    m = Model.load(staged)
    if m.signature is None or m.signature.inputs is None:
        m.signature = form_signature(ctx.spec)
        if m.signature is None:
            raise ValueError("the MLflow model has no signature (Unity Catalog requires one): "
                             "choose a Schema or Sample contract on the form")
        notes.append("signature added from the form contract")
    # A notebook-defined PythonModel cloudpickles the notebook's IPython `open`, so loading it
    # (also in Model Serving) needs IPython — add it to the copy's requirements.
    pm = (m.flavors.get("python_function") or {}).get("python_model")
    if pm and _pickle_mentions(os.path.join(staged, pm), "IPython"):
        _add_requirement(staged, "ipython")
        notes.append("added ipython to requirements (python_model.pkl references IPython)")
    with mlflow.start_run(run_name=f"{ctx.spec.get('name')}_{label}") as run:
        # Point the copy at THIS run and drop the source workspace's ids so registration never
        # looks them up.
        m.run_id, m.artifact_path = run.info.run_id, "model"
        if hasattr(m, "model_id"):
            m.model_id = None
        m.save(os.path.join(staged, "MLmodel"))
        mlflow.log_artifacts(staged, artifact_path="model")
        artifact_uri = f"{run.info.artifact_uri.rstrip('/')}/model"
    try:
        mv = mlflow.register_model(f"runs:/{run.info.run_id}/model", ctx.uc_full)
    except Exception as e:
        print(f"[register] register_model(runs:/) failed ({e}); creating the version from {artifact_uri}")
        client = MlflowClient()
        try:
            client.create_registered_model(ctx.uc_full)
        except Exception:
            pass  # already exists
        mv = client.create_model_version(name=ctx.uc_full, source=artifact_uri, run_id=run.info.run_id)
    shutil.rmtree(os.path.dirname(staged), ignore_errors=True)
    return int(mv.version), notes


def _register_code_folder(ctx, path, label):
    """A folder with model.py (a PythonModel ending with mlflow.models.set_model(...)) + artifacts
    (+ optional requirements.txt and a code/ folder of helper modules): MLflow models-from-code.
    Every entry except model.py / requirements.txt / code/ is passed as context.artifacts[<name>]."""
    path, notes = path.rstrip("/"), []
    signature = form_signature(ctx.spec)
    if signature is None:
        raise ValueError("a code folder needs a contract: an input/output schema or a sample input + output")
    artifacts = {n: os.path.join(path, n) for n in sorted(os.listdir(path))
                 if n not in CODE_FOLDER_RESERVED and not n.startswith((".", "_"))}
    # Pass the ENTRIES of code/ (not the folder) so each module lands at the root of the model's
    # code path and is importable by name.
    code_dir = os.path.join(path, "code")
    code_paths = [os.path.join(code_dir, n) for n in sorted(os.listdir(code_dir))
                  if not n.startswith((".", "__pycache__"))] if os.path.isdir(code_dir) else []
    req = os.path.join(path, "requirements.txt")
    if not os.path.exists(req):
        req = served_pip_requirements()
        notes.append("no requirements.txt in the code folder; using the job's pinned requirements")
    with mlflow.start_run(run_name=f"{ctx.spec.get('name')}_{label}"):
        # No input_example: logging one runs predict HERE, where the folder's dependencies may be
        # missing. The validate step runs it in the model's own environment instead.
        info = mlflow.pyfunc.log_model(
            name="model", python_model=os.path.join(path, "model.py"), artifacts=artifacts,
            code_paths=code_paths or None, pip_requirements=req, signature=signature,
            registered_model_name=ctx.uc_full)
    notes.append(f"code folder artifacts: {sorted(artifacts)}"
                 + (f"; code modules: {[os.path.basename(c) for c in code_paths]}" if code_paths else ""))
    return int(info.registered_model_version), notes


def _pickle_mentions(path, needle):
    """Does a pickle reference `needle` (e.g. a module)? Read via pickletools — nothing is executed."""
    try:
        with open(path, "rb") as f:
            return any(isinstance(arg, str) and needle in arg for _op, arg, _pos in pickletools.genops(f.read()))
    except Exception as e:
        print(f"[register] could not scan {path}: {e}")
        return False


def _add_requirement(model_dir, pkg):
    """Add a pip requirement to a model copy's requirements.txt and conda.yaml (idempotent)."""
    def _name(line):
        return re.split(r"[=<>!~\[ ]", str(line).strip(), maxsplit=1)[0].lower()

    req = os.path.join(model_dir, "requirements.txt")
    if os.path.exists(req):
        lines = open(req).read().splitlines()
        if pkg not in {_name(l) for l in lines}:
            with open(req, "w") as f:
                f.write("\n".join([l for l in lines if l.strip()] + [pkg]) + "\n")
    conda = os.path.join(model_dir, "conda.yaml")
    if os.path.exists(conda):
        env = yaml.safe_load(open(conda)) or {}
        for dep in env.get("dependencies", []):
            if isinstance(dep, dict) and "pip" in dep and pkg not in {_name(p) for p in dep["pip"]}:
                dep["pip"].append(pkg)
        with open(conda, "w") as f:
            yaml.safe_dump(env, f, sort_keys=False)
