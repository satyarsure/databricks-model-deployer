"""What an artifact is, and how each format becomes a Unity Catalog model version.

  file          a single pickle/joblib model file -> wrapped by the models-from-code FileModel
                (file_model.py); the file is logged as an artifact and loaded in load_context.
  mlflow_model  a folder holding an exported MLflow model (has an `MLmodel` file), possibly from
                ANOTHER workspace -> registered AS-IS (no re-wrap): its own python_model, artifacts,
                signature and environment are kept; only workspace-specific ids are reset.
  code_folder   a folder with `model.py` (a PythonModel that calls mlflow.models.set_model(...)) plus
                its artifact files/dirs and an optional requirements.txt -> MLflow models-from-code,
                every other entry is passed to load_context as context.artifacts[<name>].

To add a format: add it to ARTIFACT_FORMATS in app/server/server.ts (+ the form option), then add
detection in detect_format() and a handler in FORMAT_HANDLERS.
"""
import os
import pickletools
import shutil
import tempfile

import mlflow
import yaml
from mlflow.models import Model, infer_signature
from mlflow.tracking import MlflowClient

FILE_MODEL_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "file_model.py")
# Formats that can't be loaded inside the job's pinned environment (they bring their own
# dependencies), so they are validated in an isolated environment built from their requirements.
ISOLATED_FORMATS = {"mlflow_model", "code_folder"}
# Files never copied into a registered model (OS / editor / notebook noise).
JUNK = (".DS_Store", "._*", "__MACOSX", ".ipynb_checkpoints", "Thumbs.db")
CODE_FOLDER_RESERVED = {"model.py", "requirements.txt", "code"}


def served_pip_requirements():
    """Pin the SERVED model's environment (file format) to the exact versions in use here, so the
    serving container — rebuilt from these on scale-to-zero cold starts — always matches the
    versions the artifact was loaded/pickled with."""
    import importlib.metadata as im
    reqs = [f"mlflow=={mlflow.__version__}"]
    for p in ["scikit-learn", "pandas", "numpy", "scipy", "cloudpickle", "joblib", "xgboost", "lightgbm"]:
        try:
            reqs.append(f"{p}=={im.version(p)}")
        except Exception:
            pass
    return reqs


def detect_format(path):
    """'file' | 'mlflow_model' | 'code_folder' from what is actually at `path`."""
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


def inspect_mlflow_folder(path):
    """Read MLmodel without loading the model (its dependencies may not be installed here)."""
    m = Model.load(path)
    if "python_function" not in (m.flavors or {}):
        raise ValueError(f"{path}: MLflow model has no python_function flavor (flavors: {sorted(m.flavors or {})})")
    pf = m.flavors["python_function"]
    return {
        "has_signature": m.signature is not None and m.signature.inputs is not None,
        "mlflow_version": getattr(m, "mlflow_version", None),
        "python_version": pf.get("python_version"),
        "loader_module": pf.get("loader_module"),
    }


def pickle_references(path, needle):
    """Does a pickle reference `needle` (e.g. a module)? Read via pickletools — nothing is executed."""
    try:
        with open(path, "rb") as f:
            for _op, arg, _pos in pickletools.genops(f.read()):
                if isinstance(arg, str) and needle in arg:
                    return True
    except Exception as e:
        print(f"[formats] could not scan {path}: {e}")
    return False


def _add_requirement(model_dir, pkg):
    """Add a pip requirement to the staged copy's requirements.txt and conda.yaml (idempotent)."""
    req = os.path.join(model_dir, "requirements.txt")
    if os.path.exists(req):
        lines = [l.strip() for l in open(req).read().splitlines()]
        if not any(l.split("=")[0].split("<")[0].split(">")[0].strip().lower() == pkg for l in lines):
            with open(req, "a") as f:
                f.write(("" if not lines or lines[-1] == "" else "\n") + pkg + "\n")
    conda = os.path.join(model_dir, "conda.yaml")
    if os.path.exists(conda):
        env = yaml.safe_load(open(conda)) or {}
        for dep in env.get("dependencies", []):
            if isinstance(dep, dict) and "pip" in dep:
                if not any(str(p).split("=")[0].strip().lower() == pkg for p in dep["pip"]):
                    dep["pip"].append(pkg)
        with open(conda, "w") as f:
            yaml.safe_dump(env, f, sort_keys=False)


def _signature_from_form(contract):
    if contract.signature is not None:
        return contract.signature
    if contract.sample_input is not None and contract.sample_output is not None:
        return infer_signature(contract.sample_input, contract.sample_output)
    return None


# ---- handlers: (ctx, artifact, label, contract, served_reqs) -> (version, [notes]) ------------
def register_file(ctx, art, label, contract, served_reqs):
    from model_deployer.file_model import FileModel, load_model_file
    path, notes = art["path"], []
    sig, example = contract.signature, contract.input_example
    # Load the file here first so an artifact that isn't a loadable model fails NOW, before anything
    # is registered (FileModel itself only loads it in load_context, i.e. at serving time).
    m = FileModel()
    try:
        m.model = load_model_file(path)
    except Exception as e:
        raise ValueError(f"{path} is not a loadable pickle/joblib model file: {e}") from e
    if contract.mode == "sample" and contract.sample_input is not None:
        # Infer the signature from the REAL sample. Prefer the model's actual output (so the output
        # type is always correct); fall back to the user-provided sample output.
        out = None
        try:
            out = m.predict(None, contract.sample_input)
        except Exception as e:
            print(f"[register] sample predict failed; using provided sample output: {e}")
        if out is None:
            out = contract.sample_output
        sig = infer_signature(contract.sample_input, out)
    if sig is None:
        raise ValueError("model file needs a contract: provide an input/output schema or a sample input + output")
    with mlflow.start_run(run_name=f"{ctx.spec.get('name')}_{label}"):
        info = mlflow.pyfunc.log_model(
            name="model", python_model=FILE_MODEL_PY, artifacts={"model": path},
            signature=sig, input_example=example, pip_requirements=served_reqs,
            registered_model_name=ctx.uc_full,
        )
    return int(info.registered_model_version), notes


def register_mlflow_folder(ctx, art, label, contract, served_reqs):
    src, notes = art["path"].rstrip("/"), []
    staged = os.path.join(tempfile.mkdtemp(prefix="mlmodel_"), "model")
    shutil.copytree(src, staged, ignore=shutil.ignore_patterns(*JUNK))
    m = Model.load(staged)
    if m.signature is None or m.signature.inputs is None:
        sig = _signature_from_form(contract)
        if sig is None:
            raise ValueError("the MLflow model has no signature (Unity Catalog requires one): "
                             "choose a Schema or Sample contract on the form")
        m.signature = sig
        notes.append("signature added from the form contract")
    # A notebook-defined PythonModel cloudpickles the notebook's IPython `open`, so loading it
    # (including in Model Serving) needs IPython — add it to the model's requirements.
    pm = (m.flavors.get("python_function") or {}).get("python_model")
    if pm and pickle_references(os.path.join(staged, pm), "IPython"):
        _add_requirement(staged, "ipython")
        notes.append("added ipython to requirements (python_model.pkl references IPython)")
    with mlflow.start_run(run_name=f"{ctx.spec.get('name')}_{label}") as r:
        # The folder may have been exported from another workspace: point it at THIS run and drop
        # the source workspace's logged-model id so registration never looks them up.
        m.run_id = r.info.run_id
        m.artifact_path = "model"
        if hasattr(m, "model_id"):
            m.model_id = None
        m.save(os.path.join(staged, "MLmodel"))
        mlflow.log_artifacts(staged, artifact_path="model")
        artifact_uri = f"{r.info.artifact_uri.rstrip('/')}/model"
    try:
        mv = mlflow.register_model(f"runs:/{r.info.run_id}/model", ctx.uc_full)
    except Exception as e:
        # Fall back to creating the version straight from the run's artifact location.
        print(f"[register] register_model(runs:/) failed ({e}); creating the version from {artifact_uri}")
        client = MlflowClient()
        try:
            client.create_registered_model(ctx.uc_full)
        except Exception:
            pass
        mv = client.create_model_version(name=ctx.uc_full, source=artifact_uri, run_id=r.info.run_id)
    shutil.rmtree(os.path.dirname(staged), ignore_errors=True)
    return int(mv.version), notes


def register_code_folder(ctx, art, label, contract, served_reqs):
    path, notes = art["path"].rstrip("/"), []
    sig = _signature_from_form(contract)
    if sig is None:
        raise ValueError("a code folder needs a contract: provide an input/output schema or a sample input + output")
    artifacts = {n: os.path.join(path, n) for n in sorted(os.listdir(path))
                 if n not in CODE_FOLDER_RESERVED and not n.startswith((".", "_"))}
    # Helper modules under code/ are shipped as code_paths. Pass the ENTRIES of code/ (not the folder
    # itself) so each module / package lands at the root of the model's code path and is importable
    # as `import <module>` when the model loads.
    code_dir = os.path.join(path, "code")
    code_paths = ([os.path.join(code_dir, n) for n in sorted(os.listdir(code_dir))
                   if not n.startswith((".", "__pycache__"))] if os.path.isdir(code_dir) else None)
    req = os.path.join(path, "requirements.txt")
    if os.path.exists(req):
        reqs = req
    else:
        reqs = served_reqs
        notes.append("no requirements.txt in the code folder; using the job's pinned requirements")
    with mlflow.start_run(run_name=f"{ctx.spec.get('name')}_{label}"):
        # No input_example: logging one would run predict here, in the job's environment, which may
        # lack the folder's dependencies. The validate_isolated task runs it in the model's own env.
        info = mlflow.pyfunc.log_model(
            name="model", python_model=os.path.join(path, "model.py"), artifacts=artifacts,
            code_paths=code_paths or None,
            pip_requirements=reqs, signature=sig, registered_model_name=ctx.uc_full,
        )
    notes.append(f"code folder artifacts: {sorted(artifacts)}"
                 + (f"; code_paths: {[os.path.basename(c) for c in code_paths]}" if code_paths else ""))
    return int(info.registered_model_version), notes


FORMAT_HANDLERS = {
    "file": register_file,
    "mlflow_model": register_mlflow_folder,
    "code_folder": register_code_folder,
}
