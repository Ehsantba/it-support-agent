"""OpenAI-kompatibler Chat-Endpunkt (Gemini, lokale Server wie Ollama oder vLLM, OpenAI selbst).

Gemini: https://generativelanguage.googleapis.com/v1beta/openai  mit einem Schlüssel aus Google AI Studio.
Antworten werden als JSON angefordert (response_format json_object). Bei 429 und 5xx wird mit Wartezeit wiederholt.
"""
from __future__ import annotations

import time

import httpx

from .base import LLMError


class OpenAICompatLLM:
    def __init__(self, base_url: str, api_key: str | None, model: str, provider: str = "openai_compat",
                 min_interval: float = 0.0, max_retries: int = 5, timeout: float = 90.0,
                 client: httpx.Client | None = None, sleep=time.sleep):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key, self.model = api_key, model
        self.name = f"{provider}:{model}"
        self.min_interval, self.max_retries = min_interval, max_retries
        self.client = client or httpx.Client(timeout=timeout)
        self._sleep, self._last = sleep, 0.0

    def complete(self, system: str, user: str) -> str:
        payload = {"model": self.model, "temperature": 0, "response_format": {"type": "json_object"},
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        for attempt in range(self.max_retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = time.monotonic()
            try:
                r = self.client.post(self.url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                if attempt == self.max_retries:
                    raise LLMError(f"Sprachmodell nicht erreichbar: {type(exc).__name__}") from exc
                self._sleep(min(2 ** attempt * 2, 30))
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                if attempt == self.max_retries:
                    raise LLMError(f"Sprachmodell antwortet dauerhaft mit {r.status_code} (Kontingent erschöpft oder Dienst gestört)")
                retry_after = r.headers.get("retry-after")
                delay = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else 2 ** attempt * 4
                self._sleep(min(delay, 60))
                continue
            if r.status_code in (401, 403):
                raise LLMError("Sprachmodell lehnt den Zugang ab (401/403). Ist LLM_API_KEY bzw. GEMINI_API_KEY korrekt?")
            if r.status_code >= 400:
                raise LLMError(f"Sprachmodell-Fehler {r.status_code}: {r.text[:300]}")
            try:
                content = r.json()["choices"][0]["message"]["content"]
            except (KeyError, IndexError, ValueError) as exc:
                raise LLMError("Unerwartetes Antwortformat des Sprachmodells") from exc
            if not content:
                raise LLMError("Leere Antwort des Sprachmodells")
            return content
        raise LLMError("Sprachmodell nicht erreichbar")   # nicht erreichbar, nur für den Typprüfer
