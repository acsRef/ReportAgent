from __future__ import annotations

import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

_LLM_CONFIG = {
    "model": os.getenv("LLM_MODEL", "deepseek-chat"),
    "api_key": os.getenv("LLM_API_KEY") or os.getenv("MINIMAX_API_KEY"),
    "base_url": os.getenv("LLM_BASE_URL", "https://api.minimax.chat/v1"),
    "temperature": 0.1,
}


def get_chat_llm(**kwargs) -> ChatOpenAI:
    config = {**_LLM_CONFIG, **kwargs}
    return ChatOpenAI(**config)
