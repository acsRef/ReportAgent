"""Shared pytest fixtures for ReportAgent backend tests.

These fixtures intentionally do NOT touch the real PostgreSQL pool. Tests
that need PG should use the `persistence` marker and the `DATABASE_URL`
env var (already configured in conftest via dotenv load).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

# Ensure `backend/` is on sys.path so `from app.xxx import …` works in tests.
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Load .env from repo root so DATABASE_URL / LLM keys are present.
_REPO_ROOT = _BACKEND.parent
_ENV_PATH = _REPO_ROOT / ".env"
if _ENV_PATH.exists():
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=str(_ENV_PATH))


def pytest_collection_modifyitems(config, items):
    """Auto-skip persistence/e2e tests when required env vars are missing."""
    has_pg = bool(os.getenv("DATABASE_URL"))
    has_e2e = bool(os.getenv("REPORTAGENT_E2E"))
    skip_pg = pytest.mark.skip(reason="DATABASE_URL not set; skipping persistence test")
    skip_e2e = pytest.mark.skip(reason="REPORTAGENT_E2E not set; skipping e2e test")
    for item in items:
        if "persistence" in item.keywords and not has_pg:
            item.add_marker(skip_pg)
        if "e2e" in item.keywords and not has_e2e:
            item.add_marker(skip_e2e)


@pytest.fixture
def dummy_jwt_user() -> dict[str, Any]:
    """A stand-in for `Depends(get_current_user)` in unit tests."""
    return {"id": 1, "username": "tester"}


@pytest.fixture
def mock_pool(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A fake asyncpg pool placeholder. Real persistence tests should use
    a transactional fixture against the real `ragent-postgres` container.
    """
    class _FakePool:
        async def acquire(self):
            raise RuntimeError(
                "mock_pool.acquire() is a placeholder. "
                "Persistence tests must connect to a real PG instance."
            )
    return _FakePool()


@pytest.fixture
def pg_pool():
    """Function-scoped asyncpg pool for persistence tests.

    Each test gets its own pool + event loop, so the pool is created and
    torn down with the loop. Slow but bulletproof on Windows + asyncpg.
    """
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set; skipping persistence test")
    import asyncio
    from app.infra.db.postgres import init_pool, close_pool, get_pool

    loop = asyncio.new_event_loop()
    loop.run_until_complete(init_pool())
    try:
        yield get_pool()
    finally:
        try:
            loop.run_until_complete(close_pool())
        except Exception:
            pass
        loop.close()

