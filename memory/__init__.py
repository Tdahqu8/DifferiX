"""
DifferiX memory subsystem — public API.

Lesson nodes are MemoryNodes. Ranking is A-Score (blueprint §3).
SQLite is the runtime ranking index; lessons/*.json is the source of truth.
"""

from memory.manager import MemoryManager
from memory.ranker import (
    activation_score,
    cosine_similarity,
    rank_memory_nodes,
    rank_memory_nodes_scored,
)
from memory.schemas import (
    DimensionMismatchError,
    MemoryNode,
    MemoryNotFoundError,
    MemoryScope,
    MemoryStorageError,
    MemoryValidationError,
    RankingWeights,
    ScoredMemoryNode,
)
from memory.storage import SQLiteMemoryStorage
from memory.migrator import check_and_migrate, MigrationReport
from memory.embeddings import (
    BaseEmbeddingProvider,
    HashEmbeddingProvider,
    LocalEmbeddingProvider,
    OpenRouterEmbeddingProvider,
    build_embedding_provider,
)

__version__ = "0.4.0"

__all__ = [
    "MemoryManager",
    "SQLiteMemoryStorage",
    "MemoryNode",
    "MemoryScope",
    "RankingWeights",
    "ScoredMemoryNode",
    "activation_score",
    "cosine_similarity",
    "rank_memory_nodes",
    "rank_memory_nodes_scored",
    "MemoryStorageError",
    "DimensionMismatchError",
    "MemoryNotFoundError",
    "MemoryValidationError",
    "BaseEmbeddingProvider",
    "HashEmbeddingProvider",
    "LocalEmbeddingProvider",
    "OpenRouterEmbeddingProvider",
    "build_embedding_provider",
    "check_and_migrate",
    "MigrationReport",
]