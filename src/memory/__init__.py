"""Memory subsystem (incremental rebuild).

Intentionally minimal during the layered rebuild: the package root does NOT
re-export anything yet, so modules can be pulled in a few at a time and imported
individually (e.g. `from memory.memory_record import ...`) without dragging in
the whole 105-file subsystem before it exists.

The full public API (MemoryKernel, MemoryRuntimeServices, LivingUser*, ...) will
be restored here once the modules it re-exports have all landed.
"""

__all__: list[str] = []
