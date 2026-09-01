"""Observability 适配层（P13）：Langfuse 接入 + PII redaction。

伞形 plan §十三 / §P13：tracer 双 sink (PG + Langfuse) + Adapter 前 PII mask。
Task 1 先落地 LangfuseConfig + client 单例；redaction 导出由 Task 2 补齐。
"""
from app.observability.langfuse_client import get_langfuse_client
from app.observability.langfuse_config import LangfuseConfig

__all__ = ["LangfuseConfig", "get_langfuse_client"]