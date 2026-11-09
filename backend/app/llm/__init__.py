from __future__ import annotations

from ..config import Settings
from .base import LLM, LLMError
from .mock import MockLLM
from .openai_compat import OpenAICompatLLM

__all__ = ["LLM", "LLMError", "MockLLM", "OpenAICompatLLM", "build_llm"]


def build_llm(s: Settings) -> LLM:
    p = s.llm_provider
    if p in ("mock", "mock-naiv", "naive-mock"):
        return MockLLM(naive=p != "mock")
    if p in ("gemini", "openai_compat"):
        if p == "gemini" and (not s.llm_api_key or s.llm_api_key.startswith("HIER_")):
            raise LLMError("Für Gemini fehlt der Schlüssel. Trage GEMINI_API_KEY in die Datei .env ein (siehe .env.example).")
        if not s.llm_base_url:
            raise LLMError("LLM_BASE_URL fehlt.")
        return OpenAICompatLLM(s.llm_base_url, s.llm_api_key, s.llm_model, provider=p, min_interval=s.llm_min_interval)
    raise LLMError(f"Unbekannter LLM_PROVIDER: {p} (erlaubt: mock, mock-naiv, gemini, openai_compat)")
