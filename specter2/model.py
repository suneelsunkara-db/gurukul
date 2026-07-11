"""SPECTER2 embedding model for Databricks Model Serving (MLflow pyfunc).

Packages `allenai/specter2_base` with two adapters loaded on one endpoint:
  - proximity   -> encode candidate PAPERS and full title+abstract queries.
                   This is the variant Semantic Scholar uses for its
                   precomputed vectors, so our vectors live in the SAME space.
  - adhoc_query -> encode SHORT raw-text queries (e.g. a seed like "RLHF").
                   Outputs are designed to be matched against proximity-encoded
                   candidates.

Input  (pandas DataFrame or dict): columns `text` (str) and optional
        `adapter` (str, "proximity" | "adhoc_query"; default "proximity").
Output: list[list[float]] - one 768-d embedding per input row.

Uses the models-from-code pattern (set_model at import) so nothing is pickled.
"""

from __future__ import annotations

import collections

import mlflow

DIM = 768
_VALID_ADAPTERS = ("proximity", "adhoc_query")


class Specter2Model(mlflow.pyfunc.PythonModel):
    def load_context(self, context):
        import torch
        from adapters import AutoAdapterModel
        from transformers import AutoTokenizer

        base = context.artifacts["base"]
        self.tokenizer = AutoTokenizer.from_pretrained(base)
        self.model = AutoAdapterModel.from_pretrained(base)

        self._loaded = []
        for name in _VALID_ADAPTERS:
            path = context.artifacts.get(name)
            if path:
                self.model.load_adapter(path, load_as=name, set_active=False)
                self._loaded.append(name)
        if not self._loaded:
            raise RuntimeError("No SPECTER2 adapters found in artifacts")

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model.to(self.device)
        self.model.eval()

    def _encode(self, texts, adapter):
        import torch

        name = adapter if adapter in self._loaded else "proximity"
        if name not in self._loaded:
            name = self._loaded[0]
        self.model.set_active_adapters(name)
        with torch.no_grad():
            inputs = self.tokenizer(
                texts,
                padding=True,
                truncation=True,
                return_tensors="pt",
                return_token_type_ids=False,
                max_length=512,
            ).to(self.device)
            out = self.model(**inputs)
            # SPECTER2 uses the first ([CLS]) token as the embedding.
            return out.last_hidden_state[:, 0, :].cpu().numpy()

    def predict(self, context, model_input, params=None):
        import pandas as pd

        if isinstance(model_input, dict):
            model_input = pd.DataFrame(model_input)

        texts = [str(t) for t in model_input["text"].tolist()]
        if "adapter" in model_input.columns:
            adapters = [
                (str(a) if a is not None else "proximity")
                for a in model_input["adapter"].tolist()
            ]
        else:
            adapters = ["proximity"] * len(texts)

        # Group by adapter to avoid switching the active adapter per row.
        groups: dict[str, list[int]] = collections.defaultdict(list)
        for i, a in enumerate(adapters):
            groups[a].append(i)

        result: list[list[float]] = [None] * len(texts)  # type: ignore[list-item]
        for adapter, idxs in groups.items():
            emb = self._encode([texts[i] for i in idxs], adapter)
            for j, i in enumerate(idxs):
                result[i] = emb[j].tolist()
        return result


mlflow.models.set_model(Specter2Model())
