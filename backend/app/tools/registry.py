from __future__ import annotations

from typing import Any, Callable, Optional

from pydantic import BaseModel


class ToolMetadata(BaseModel):
    name: str
    description: str
    capability: str
    agent_type: str
    source: str = "local"
    permission_required: list[str] = []
    input_schema: dict = {}
    output_schema: dict = {}


class ToolRegistry:
    def __init__(self, permission_checker: Any = None):
        self._tools: dict[str, ToolMetadata] = {}
        self._instances: dict[str, Callable] = {}
        self._checker = permission_checker

    def register(self, name: str, tool_fn: Callable, metadata: ToolMetadata):
        self._tools[name] = metadata
        self._instances[name] = tool_fn

    def get(self, names: list[str], user_context: Optional[dict] = None) -> list[Callable]:
        if user_context is None or self._checker is None:
            return [self._instances[n] for n in names if n in self._instances]
        allowed = []
        for name in names:
            meta = self._tools.get(name)
            if meta and self._checker.check(user_context, meta.permission_required):
                allowed.append(self._instances[name])
        return allowed

    def get_metadata(self, name: str) -> Optional[ToolMetadata]:
        return self._tools.get(name)

    def list_by_capability(self, capability: str) -> list[ToolMetadata]:
        return [m for m in self._tools.values() if m.capability == capability]

    def list_by_agent(self, agent_type: str) -> list[ToolMetadata]:
        return [m for m in self._tools.values() if m.agent_type == agent_type]

    def all_tools(self) -> dict[str, ToolMetadata]:
        return dict(self._tools)


registry = ToolRegistry()
