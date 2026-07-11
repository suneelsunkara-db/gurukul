"""Download SPECTER2 weights + adapters and register an MLflow pyfunc model
to the Unity Catalog model registry.

Run via scripts/deploy_specter2.sh (which supplies the right deps). Direct:

    uv run --with-requirements specter2/requirements.txt \
        python -m specter2.register_specter2 --uc-model main.default.gurukul_specter2

Prints the registered model version to stdout (last line) so the deploy
script can wire it into the serving endpoint.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile

import mlflow
import pandas as pd
from mlflow.models import infer_signature

BASE_REPO = "allenai/specter2_base"
ADAPTER_REPOS = {
    "proximity": "allenai/specter2",
    "adhoc_query": "allenai/specter2_adhoc_query",
}


def _download_artifacts(dst: str) -> dict[str, str]:
    """Download base encoder + both adapters into local dirs (no internet at
    serving time - everything ships as MLflow artifacts)."""
    from adapters import AutoAdapterModel
    from transformers import AutoTokenizer

    base_dir = os.path.join(dst, "base")
    print(f"Downloading base encoder {BASE_REPO} ...", file=sys.stderr)
    AutoTokenizer.from_pretrained(BASE_REPO).save_pretrained(base_dir)
    model = AutoAdapterModel.from_pretrained(BASE_REPO)
    model.save_pretrained(base_dir)

    artifacts = {"base": base_dir}
    for name, repo in ADAPTER_REPOS.items():
        print(f"Downloading adapter {name} <- {repo} ...", file=sys.stderr)
        model.load_adapter(repo, load_as=name, set_active=False)
        adir = os.path.join(dst, name)
        model.save_adapter(adir, name)
        artifacts[name] = adir
    return artifacts


def register_model(uc_model: str, experiment_name: str | None = None) -> int:
    """Download artifacts, register the pyfunc model, and return latest version."""
    mlflow.set_tracking_uri("databricks")
    mlflow.set_registry_uri("databricks-uc")
    experiment_id: str | None = None
    if experiment_name:
        tracking_client = mlflow.MlflowClient(tracking_uri="databricks")
        exp = tracking_client.get_experiment_by_name(experiment_name)
        if exp is None:
            experiment_id = tracking_client.create_experiment(experiment_name)
            print(
                f"Created MLflow experiment {experiment_name} ({experiment_id})",
                file=sys.stderr,
            )
        else:
            experiment_id = exp.experiment_id
            print(
                f"Using MLflow experiment {experiment_name} ({experiment_id})",
                file=sys.stderr,
            )
        mlflow.set_experiment(experiment_name)
        resolved = mlflow.get_experiment_by_name(experiment_name)
        if resolved is None or resolved.experiment_id is None:
            raise RuntimeError(
                f"MLflow experiment did not resolve: {experiment_name!r}"
            )
        experiment_id = resolved.experiment_id

    model_py = os.path.join(os.path.dirname(__file__), "model.py")
    req_txt = os.path.join(os.path.dirname(__file__), "requirements.txt")

    example = pd.DataFrame(
        {
            "text": ["BERT: pre-training of deep bidirectional transformers"],
            "adapter": ["proximity"],
        }
    )
    signature = infer_signature(example, [[0.0] * 768])

    with tempfile.TemporaryDirectory() as tmp:
        artifacts = _download_artifacts(tmp)
        print("Logging + registering model to Unity Catalog ...", file=sys.stderr)
        with mlflow.start_run(experiment_id=experiment_id, run_name="specter2-embedder"):
            info = mlflow.pyfunc.log_model(
                artifact_path="model",
                python_model=model_py,  # models-from-code (no pickling)
                artifacts=artifacts,
                signature=signature,
                input_example=example,
                pip_requirements=req_txt,
                registered_model_name=uc_model,
            )

    client = mlflow.MlflowClient(registry_uri="databricks-uc")
    versions = client.search_model_versions(f"name='{uc_model}'")
    latest = max(int(v.version) for v in versions)
    print(f"Registered {uc_model} version {latest} (run {info.run_id})", file=sys.stderr)
    return latest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uc-model", required=True, help="catalog.schema.name")
    ap.add_argument("--experiment-name")
    args = ap.parse_args()

    latest = register_model(args.uc_model, experiment_name=args.experiment_name)
    # Last stdout line = the version, for the deploy script to capture.
    print(latest)


if __name__ == "__main__":
    main()
