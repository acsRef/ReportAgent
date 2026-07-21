from __future__ import annotations

import os
import re

from langchain_openai import ChatOpenAI

_LLM_CONFIG = {
    "model": os.getenv("LLM_MODEL", "MiniMax-M2.7-highspeed"),
    "api_key": os.getenv("LLM_API_KEY") or os.getenv("MINIMAX_API_KEY"),
    "base_url": os.getenv("LLM_BASE_URL", "https://api.minimax.chat/v1"),
    "temperature": 0.1,
}


def get_chat_llm(**kwargs) -> ChatOpenAI:
    config = {**_LLM_CONFIG, **kwargs}
    return ChatOpenAI(**config)


def call_llm(prompt: str | list, **kwargs) -> str:
    llm = get_chat_llm(**kwargs)
    resp = llm.invoke(prompt)
    text = resp.content or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return text.strip()
