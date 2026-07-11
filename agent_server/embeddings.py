"""SPECTER2 embedding client.

Queries the SPECTER2 Model Serving endpoint (deployed via
scripts/deploy_specter2.sh). One embedding space powers both seed
resolution and the paper corpus:

  - short seeds  -> `adhoc_query` adapter   (embed_seed)
  - papers       -> `proximity`  adapter    (embed_paper / embed_papers)

The `proximity` variant matches Semantic Scholar's precomputed vectors,
so query and corpus vectors are directly comparable.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os

from databricks.sdk import WorkspaceClient

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gurukul-specter2-embed")
EMBED_DIM = 768
# SPECTER2 encodes a paper as `title <sep> abstract`. HF fast tokenizers
# parse this literal special token out of the raw string.
_SEP = "[SEP]"

_ws: WorkspaceClient | None = None


def _get_ws() -> WorkspaceClient:
    global _ws
    if _ws is None:
        _ws = WorkspaceClient()
    return _ws


def _require_endpoint() -> str:
    if not EMBEDDING_MODEL:
        raise RuntimeError(
            "EMBEDDING_MODEL not set - deploy the SPECTER2 endpoint "
            "(scripts/deploy_specter2.sh) and set EMBEDDING_MODEL."
        )
    return EMBEDDING_MODEL


def _query_sync(records: list[dict]) -> list[list[float]]:
    resp = _get_ws().serving_endpoints.query(
        name=_require_endpoint(),
        dataframe_records=records,
    )
    preds = resp.predictions
    if not isinstance(preds, list):
        raise RuntimeError(f"Unexpected embedding response: {type(preds)}")
    return preds


async def embed_texts(texts: list[str], adapter: str = "proximity") -> list[list[float]]:
    """Embed a batch of raw texts with the given adapter."""
    if not texts:
        return []
    records = [{"text": t, "adapter": adapter} for t in texts]
    return await asyncio.to_thread(_query_sync, records)


async def embed_seed(seed: str) -> list[float]:
    """Embed a short seed query (adhoc_query adapter)."""
    out = await embed_texts([seed], adapter="adhoc_query")
    return out[0]


async def embed_paper(title: str, abstract: str | None = None) -> list[float]:
    """Embed a single paper (proximity adapter)."""
    out = await embed_papers([(title, abstract or "")])
    return out[0]


async def embed_papers(items: list[tuple[str, str]]) -> list[list[float]]:
    """Embed papers given (title, abstract) tuples (proximity adapter)."""
    texts = [f"{title}{_SEP}{abstract or ''}" for title, abstract in items]
    return await embed_texts(texts, adapter="proximity")


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors (0 if either is degenerate)."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
