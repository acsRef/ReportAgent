"""State 五块归位 contract 模块（P3 Task 1）。

Re-export 5 TypedDict + split/merge（plan §F8）：调用方可直接
`from app.state import RequestState, split_state, merge_state`，
不必每次穿透到 `app.state.blocks` 写完整路径。
"""
from app.state.blocks import (
    ExecutionState,
    ReportState,
    RequestState,
    RequirementState,
    RuntimeState,
    merge_state,
    split_state,
)

__all__ = [
    "ExecutionState",
    "ReportState",
    "RequestState",
    "RequirementState",
    "RuntimeState",
    "merge_state",
    "split_state",
]