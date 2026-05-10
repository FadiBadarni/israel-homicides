"""Test-suite shared config.

Async-test compatibility: if pytest-asyncio is not installed, wrap any test
coroutine so it runs to completion via ``asyncio.run()``. This keeps the
suite dependency-light without giving up on async tests.
"""
from __future__ import annotations

import asyncio
import inspect


try:
    import pytest_asyncio  # noqa: F401
    _HAS_PYTEST_ASYNCIO = True
except ImportError:
    _HAS_PYTEST_ASYNCIO = False


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "asyncio: run an async test (compat shim — pytest-asyncio not required)",
    )


def pytest_collection_modifyitems(config, items):
    if _HAS_PYTEST_ASYNCIO:
        return

    for item in items:
        test_fn = getattr(item, "obj", None)
        if test_fn is None or not inspect.iscoroutinefunction(test_fn):
            continue

        def _make_runner(coro_func):
            def runner(*args, **kwargs):
                return asyncio.run(coro_func(*args, **kwargs))

            runner.__wrapped__ = coro_func
            runner.__name__ = coro_func.__name__
            return runner

        item.obj = _make_runner(test_fn)
