from __future__ import annotations

import os


class LLMConfig:
    def __init__(self) -> None:
        self.model: str = os.getenv("LLM_MODEL") or os.getenv("MINIMAX_MODEL") or "deepseek-ai/DeepSeek-V3"
        self.api_key: str | None = os.getenv("LLM_API_KEY") or os.getenv("MINIMAX_API_KEY")
        self.base_url: str = os.getenv("LLM_BASE_URL") or os.getenv("MINIMAX_BASE_URL") or "https://api.siliconflow.cn/v1"
        self.provider: str = os.getenv("LLM_PROVIDER", "siliconflow")
        self.timeout: float = float(os.getenv("LLM_TIMEOUT", "60"))
        # 宪法 §11 契约值：LLM transient retry 预算 2（P9 自 P6 遗留默认 5 收敛）。
        self.max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "2"))
        self.max_total_time: float = float(os.getenv("LLM_MAX_TOTAL_TIME", "90"))
        self.context_window: int = int(os.getenv("LLM_CONTEXT_WINDOW", "131072"))
        self.temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.1"))

    def to_chat_kwargs(self) -> dict:
        return {
            "model": self.model,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "temperature": self.temperature,
            "max_retries": 0,
            "timeout": self.timeout,
        }
