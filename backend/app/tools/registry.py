from __future__ import annotations

from typing import Any, Callable, Optional

from pydantic import BaseModel, Field


class ToolMetadata(BaseModel):
    model_config = {"populate_by_name": True}

    name: str
    description: str = ""
    purpose: str = ""
    when_to_use: str = ""
    when_not_to_use: str = ""
    input_schema: dict = Field(default_factory=dict)
    output_schema: dict = Field(default_factory=dict)
    preconditions: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)
    failure_policy: str = ""
    side_effects: str = ""
    examples: list[str] = Field(default_factory=list)
    risk_level: str = "low"
    permission: list[str] = Field(default_factory=list)
    permission_required: list[str] = Field(default_factory=list)
    source: str = "local"
    capability: str = ""
    agent_type: str = ""

    def model_post_init(self, __context) -> None:
        if self.permission and not self.permission_required:
            self.permission_required = list(self.permission)
        elif self.permission_required and not self.permission:
            self.permission = list(self.permission_required)


class ToolRegistry:
    def __init__(self, permission_checker: Any = None):
        self._tools: dict[str, ToolMetadata] = {}
        self._instances: dict[str, Callable] = {}
        self._checker = permission_checker

    def register(self, name: str, tool_fn: Callable, metadata: ToolMetadata):
        self._tools[name] = metadata
        self._instances[name] = tool_fn

    def validate(self) -> list[str]:
        errors: list[str] = []
        for name, meta in self._tools.items():
            if not meta.purpose:
                errors.append(f"{name}: purpose empty")
            if not meta.when_to_use:
                errors.append(f"{name}: when_to_use empty")
            if not meta.when_not_to_use:
                errors.append(f"{name}: when_not_to_use empty")
            if not isinstance(meta.input_schema, dict):
                errors.append(f"{name}: input_schema not dict")
            if not isinstance(meta.output_schema, dict):
                errors.append(f"{name}: output_schema not dict")
            if not meta.preconditions:
                errors.append(f"{name}: preconditions empty")
            if not meta.postconditions:
                errors.append(f"{name}: postconditions empty")
            if not meta.failure_policy:
                errors.append(f"{name}: failure_policy empty")
            if not meta.side_effects:
                errors.append(f"{name}: side_effects empty")
            if not meta.examples:
                errors.append(f"{name}: examples empty")
            if meta.risk_level not in {"low", "medium", "high"}:
                errors.append(f"{name}: risk_level invalid {meta.risk_level!r}")
            if meta.source not in {"local", "mcp"}:
                errors.append(f"{name}: source invalid {meta.source!r}")
            if not meta.name:
                errors.append(f"{name}: name empty")
            if "什么时候调" not in meta.description or "什么时候不调" not in meta.description:
                errors.append(f"{name}: description missing 四问")
            if "调用前" not in meta.description or "调用后" not in meta.description:
                errors.append(f"{name}: description missing 调用前/后")
        return errors

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
