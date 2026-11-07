from __future__ import annotations

import asyncio
import inspect
import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "asyncio: run coroutine test in an asyncio event loop")


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    if inspect.iscoroutinefunction(pyfuncitem.obj):
        sig = inspect.signature(pyfuncitem.obj)
        kwargs = {k: v for k, v in pyfuncitem.funcargs.items() if k in sig.parameters}
        asyncio.run(pyfuncitem.obj(**kwargs))
        return True
    return None
