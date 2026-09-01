from __future__ import annotations

import os

from pydantic import BaseModel


class LangfuseConfig(BaseModel):
    """Langfuse SDK 配置（env-gated）。

    关键：`LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` 都设时 enabled=True，
    否则整条 Langfuse 路径跳过，仅 PG sink 生效（fail-open，observability
    是 best-effort 不阻塞主流程）。
    """
    enabled: bool = False
    public_key: str | None = None
    secret_key: str | None = None
    host: str = "https://cloud.langfuse.com"
    flush_timeout: float = 10.0

    def __init__(self, **data):
        super().__init__(**data)
        if not self.enabled:
            # env 探测
            pk = os.getenv("LANGFUSE_PUBLIC_KEY")
            sk = os.getenv("LANGFUSE_SECRET_KEY")
            if pk and sk:
                self.enabled = True
                self.public_key = pk
                self.secret_key = sk
        self.host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
        self.flush_timeout = float(os.getenv("LANGFUSE_FLUSH_TIMEOUT", "10.0"))