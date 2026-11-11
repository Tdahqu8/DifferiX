"""
Data types, enums, and exceptions for the DifferiX memory subsystem.
"""

from __future__ import annotations

import enum
import math
import time
import uuid
from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class MemoryStorageError(Exception):
    """Base exception for all memory storage operations."""


class DimensionMismatchError(MemoryStorageError):
    def __init__(self, expected: int, received: int) -> None:
        self.expected = expected
        self.received = received
        super().__init__(
            f"Vector dimension mismatch: expected {expected}, received {received}."
        )


class MemoryNotFoundError(MemoryStorageError):
    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        super().__init__(f"Memory node not found: {node_id}")


class MemoryValidationError(MemoryStorageError):
    """Raised when a MemoryNode violates schema-level invariants."""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class MemoryScope(str, enum.Enum):
    GLOBAL = "global"
    PROJECT = "project"
    LANGUAGE = "language"
    DOMAIN = "domain"
    FILE = "file"


# ---------------------------------------------------------------------------
# Core model
# ---------------------------------------------------------------------------
class MemoryNode(BaseModel):
    """A single memory node / lesson with A-Score signals."""

    node_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    vector: List[float] = Field(...)
    content: str = Field(..., min_length=1)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    base_trust: float = Field(default=0.3, ge=0.0, le=1.0)
    trigger_count: int = Field(default=0, ge=0)
    helpful_count: int = Field(default=0, ge=0)
    violation_count: int = Field(default=0, ge=0)
    is_critical: bool = Field(default=False)

    scope_type: MemoryScope = Field(default=MemoryScope.GLOBAL)
    scope_value: str = Field(default="*", min_length=1)

    created_at: float = Field(default_factory=time.time)
    last_used_at: float = Field(default_factory=time.time)

    @field_validator("vector")
    @classmethod
    def _validate_vector(cls, v: List[float]) -> List[float]:
        if not v:
            raise MemoryValidationError("Vector cannot be empty.")
        for idx, value in enumerate(v):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise MemoryValidationError(
                    f"Vector element at index {idx} is not numeric: {value!r}"
                )
            if math.isnan(value) or math.isinf(value):
                raise MemoryValidationError(
                    f"Vector contains NaN/Inf at index {idx}: {value!r}"
                )
        return [float(x) for x in v]

    model_config = ConfigDict(frozen=False, extra="forbid")


# ---------------------------------------------------------------------------
# Ranking weights (A-Score hyperparameters)
# ---------------------------------------------------------------------------
class RankingWeights(BaseModel):
    alpha: float = Field(default=1.0, ge=0.0, le=5.0)
    beta_protection: float = Field(default=2.0, ge=0.0, le=10.0)
    lambda_base: float = Field(default=0.01, gt=0.0)
    epsilon: float = Field(default=1e-5, ge=0.0)

    model_config = ConfigDict(frozen=False, extra="forbid")


class ScoredMemoryNode(BaseModel):
    node: MemoryNode
    score: float
    similarity: float
    scope_match: float
    decay: float
    jitter: float

    model_config = ConfigDict(frozen=False, extra="forbid")


__all__ = [
    "MemoryNode",
    "MemoryScope",
    "RankingWeights",
    "ScoredMemoryNode",
    "MemoryStorageError",
    "DimensionMismatchError",
    "MemoryNotFoundError",
    "MemoryValidationError",
]