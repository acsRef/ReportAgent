from __future__ import annotations

import os

from langgraph.checkpoint.memory import MemorySaver


def create_checkpointer(env: str | None = None):
    if env is None:
        env = os.getenv("APP_ENV", "dev")

    if env == "dev":
        return MemorySaver()
    elif env == "production":
        # TODO: replace with AsyncPostgresSaver when PostgreSQL checkpoint is implemented
        # from langgraph.checkpoint.postgres import AsyncPostgresSaver
        # return AsyncPostgresSaver(...)
        raise NotImplementedError("Production checkpointer not yet configured")
    else:
        return MemorySaver()
