from __future__ import annotations
from core.di import resolve_dependency

"""
memory.memory_services
======================

Composition root cho memory runtime.

Code ứng dụng nên tạo dependency ở đây rồi truyền xuống, thay vì mỗi module tự lấy
một global registry/embedding model. API legacy vẫn chạy nhờ default proxy, nhưng
integration mới có một graph dependency rõ ràng và test được độc lập.
"""

from dataclasses import dataclass
from typing import Any

from .claim_extractor import ClaimExtractor
from .claims.claim_builder import ClaimBuilder
from .memory_cognition import CommitPolicy, MemoryCognitionCore
from .memory_kernel import MemoryKernel
from .memory_kernel_contracts import MemoryKernelConfig
from .memory_query_compiler import MemoryQueryCompiler
from .memory_query_executor import MemoryQueryExecutor
from .memory_recall_policy import MemoryRecallSurfacePolicy
from .predicate_registry import PredicateRegistry
from .understanding.semantic_parser import FallbackSurfaceSemanticParser, SemanticParser


@dataclass(slots=True)
class MemoryRuntimeServices:
    """Nhóm service dùng chung cùng một registry và embedding backend."""

    registry: PredicateRegistry
    embedding_index: Any | None
    claim_builder: ClaimBuilder
    claim_extractor: ClaimExtractor
    query_compiler: MemoryQueryCompiler
    query_executor: MemoryQueryExecutor
    surface_policy: MemoryRecallSurfacePolicy

    @classmethod
    def build(
        cls,
        *,
        registry_path: str | None = None,
        embedding_index: Any | None = None,
        semantic_parser: SemanticParser | None = None,
    ) -> "MemoryRuntimeServices":
        registry = PredicateRegistry(persistence_path=registry_path)
        parser = resolve_dependency(semantic_parser, FallbackSurfaceSemanticParser)
        builder = ClaimBuilder(registry=registry)
        return cls(
            registry=registry,
            embedding_index=embedding_index,
            claim_builder=builder,
            claim_extractor=ClaimExtractor(parser=parser, builder=builder),
            query_compiler=MemoryQueryCompiler(registry=registry),
            query_executor=MemoryQueryExecutor(registry=registry),
            surface_policy=MemoryRecallSurfacePolicy(registry=registry),
        )

    def create_core(
        self,
        base_dir: str,
        *,
        policy: CommitPolicy | None = None,
    ) -> MemoryCognitionCore:
        return MemoryCognitionCore(
            base_dir,
            policy=policy,
            registry=self.registry,
            embedding_index=self.embedding_index,
        )

    def create_kernel(
        self,
        base_dir: str,
        *,
        semantic_parser: Any | None = None,
        config: MemoryKernelConfig | None = None,
        commit_policy: CommitPolicy | None = None,
    ) -> MemoryKernel:
        core = self.create_core(base_dir, policy=commit_policy)
        return MemoryKernel(
            core=core,
            semantic_parser=semantic_parser,
            config=config,
            registry=self.registry,
            query_compiler=self.query_compiler,
            query_executor=self.query_executor,
            surface_policy=self.surface_policy,
        )


__all__ = ["MemoryRuntimeServices"]
