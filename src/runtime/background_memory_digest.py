from __future__ import annotations

from typing import Any

try:
    from memory.memory_kernel import MemoryKernel
    from runtime.background_memory_digest_kernel import (
        BackgroundMemoryDigestKernelConfig as BackgroundMemoryDigestConfig,
        BackgroundMemoryDigestKernelService as BackgroundMemoryDigestService,
        MemoryDigestKernelJob as MemoryDigestJob,
    )
except Exception:  # pragma: no cover
    from ..memory.memory_kernel import MemoryKernel  # type: ignore
    from .background_memory_digest_kernel import (  # type: ignore
        BackgroundMemoryDigestKernelConfig as BackgroundMemoryDigestConfig,
        BackgroundMemoryDigestKernelService as BackgroundMemoryDigestService,
        MemoryDigestKernelJob as MemoryDigestJob,
    )


def build_background_memory_digest_service(
    *,
    memory_kernel: MemoryKernel | None = None,
    semantic_parser: Any = None,
    state_committer_factory: Any = None,
    memory_core: Any | None = None,
    logger: Any | None = None,
    config: BackgroundMemoryDigestConfig | None = None,
    idle_probe: Any | None = None,
) -> BackgroundMemoryDigestService | None:
    """Compat builder: ưu tiên MemoryKernel, bỏ qua hook commit cũ."""

    del state_committer_factory
    kernel = memory_kernel
    if kernel is None and memory_core is not None:
        try:
            kernel = MemoryKernel.from_existing_core(memory_core, semantic_parser=semantic_parser)
        except Exception:
            kernel = None
    if kernel is None:
        return None
    service = BackgroundMemoryDigestService(
        kernel,
        config=config,
        logger=logger,
        idle_probe=idle_probe,
    )
    if not service.enabled:
        return None
    service.start()
    return service


__all__ = [
    "BackgroundMemoryDigestConfig",
    "BackgroundMemoryDigestService",
    "MemoryDigestJob",
    "build_background_memory_digest_service",
]
