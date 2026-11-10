"""
Activation Score (A-Score) ranking for MemoryNode candidates.

    A = [ (B · (1 + H) · Sim^α · ScopeMatch) / (T + 2 + V) ]
        · e^(−λ_eff · Δt)  +  ε

    λ_eff = λ_base / (1 + β · 1_critical)
"""

from __future__ import annotations

import hashlib
import math
import time
from typing import List, Optional, Sequence, Tuple

from memory.schemas import (
    DimensionMismatchError,
    MemoryNode,
    MemoryScope,
    MemoryStorageError,
    RankingWeights,
    ScoredMemoryNode,
)


def _validate_vector(vector: Sequence[float], label: str) -> None:
    if not vector:
        raise MemoryStorageError(f"{label} must be non-empty.")
    for idx, value in enumerate(vector):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise MemoryStorageError(
                f"{label} element at index {idx} is not numeric: {value!r}"
            )
        if math.isnan(value) or math.isinf(value):
            raise MemoryStorageError(
                f"{label} contains NaN/Inf at index {idx}: {value!r}"
            )


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise DimensionMismatchError(len(a), len(b))
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    raw = dot / (math.sqrt(na) * math.sqrt(nb))
    return max(-1.0, min(1.0, raw))


def _normalized_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    return (cosine_similarity(a, b) + 1.0) / 2.0


def _deterministic_jitter(node_id: str, scale: float) -> float:
    if scale <= 0.0:
        return 0.0
    digest = hashlib.sha256(node_id.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
    return scale * (2.0 * bucket - 1.0)


def _scope_match(
    node_scope: MemoryScope,
    node_value: str,
    query_scope: Optional[MemoryScope],
    query_value: Optional[str],
) -> float:
    if node_scope == MemoryScope.GLOBAL:
        return 1.0
    if query_scope is None:
        return 1.0
    if node_scope == query_scope and node_value == query_value:
        return 1.0
    return 0.5


def activation_score(
    node: MemoryNode,
    query_vector: Sequence[float],
    weights: Optional[RankingWeights] = None,
    query_scope: Optional[MemoryScope] = None,
    query_scope_value: Optional[str] = None,
    reference_time: Optional[float] = None,
) -> Tuple[float, float, float, float, float]:
    if weights is None:
        weights = RankingWeights()
    if len(node.vector) != len(query_vector):
        raise DimensionMismatchError(len(node.vector), len(query_vector))

    sim = _normalized_similarity(query_vector, node.vector)
    sim_term = sim ** float(weights.alpha)
    sm = _scope_match(node.scope_type, node.scope_value, query_scope, query_scope_value)

    numerator = (
        float(node.base_trust)
        * (1.0 + float(node.helpful_count))
        * sim_term
        * sm
    )
    denominator = float(node.trigger_count) + 2.0 + float(node.violation_count)

    now = time.time() if reference_time is None else float(reference_time)
    delta_hours = max(0.0, (now - float(node.last_used_at)) / 3600.0)
    protection = 1.0 + weights.beta_protection * (1.0 if node.is_critical else 0.0)
    lambda_eff = float(weights.lambda_base) / protection
    decay = math.exp(-lambda_eff * delta_hours)

    jitter = _deterministic_jitter(node.node_id, float(weights.epsilon))
    score = max(0.0, (numerator / denominator) * decay + jitter)
    return score, sim, sm, decay, jitter


def _score_and_sort(
    nodes: Sequence[MemoryNode],
    query_vector: Sequence[float],
    weights: RankingWeights,
    query_scope: Optional[MemoryScope],
    query_scope_value: Optional[str],
    reference_time: Optional[float],
) -> List[ScoredMemoryNode]:
    scored: List[ScoredMemoryNode] = []
    for node in nodes:
        if not isinstance(node, MemoryNode):
            raise MemoryStorageError(
                f"ranker received non-MemoryNode: {type(node).__name__}"
            )
        score, sim, sm, decay, jitter = activation_score(
            node=node,
            query_vector=query_vector,
            weights=weights,
            query_scope=query_scope,
            query_scope_value=query_scope_value,
            reference_time=reference_time,
        )
        scored.append(
            ScoredMemoryNode(
                node=node,
                score=score,
                similarity=sim,
                scope_match=sm,
                decay=decay,
                jitter=jitter,
            )
        )
    scored.sort(key=lambda s: (-s.score, s.node.node_id))
    return scored


def rank_memory_nodes(
    nodes: List[MemoryNode],
    query_vector: List[float],
    weights: Optional[RankingWeights] = None,
    query_scope: Optional[MemoryScope] = None,
    query_scope_value: Optional[str] = None,
    top_k: Optional[int] = None,
    reference_time: Optional[float] = None,
) -> List[MemoryNode]:
    if not nodes:
        return []
    _validate_vector(query_vector, "query_vector")
    eff = weights or RankingWeights()
    scored = _score_and_sort(
        nodes, query_vector, eff, query_scope, query_scope_value, reference_time
    )
    if top_k is not None:
        scored = scored[: max(0, top_k)]
    return [s.node for s in scored]


def rank_memory_nodes_scored(
    nodes: List[MemoryNode],
    query_vector: List[float],
    weights: Optional[RankingWeights] = None,
    query_scope: Optional[MemoryScope] = None,
    query_scope_value: Optional[str] = None,
    top_k: Optional[int] = None,
    reference_time: Optional[float] = None,
) -> List[ScoredMemoryNode]:
    if not nodes:
        return []
    _validate_vector(query_vector, "query_vector")
    eff = weights or RankingWeights()
    scored = _score_and_sort(
        nodes, query_vector, eff, query_scope, query_scope_value, reference_time
    )
    if top_k is not None:
        scored = scored[: max(0, top_k)]
    return scored


__all__ = [
    "cosine_similarity",
    "activation_score",
    "rank_memory_nodes",
    "rank_memory_nodes_scored",
]