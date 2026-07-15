from __future__ import annotations

import json
import re
from typing import Any

import httpx

from oplab.config import Settings


class ModelUnavailableError(RuntimeError):
    pass


class ModelGateway:
    """Small OpenAI-compatible port; phase A remains runnable without a model key."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._client = client

    @property
    def enabled(self) -> bool:
        return bool(self.settings.openai_api_key)

    async def complete_json(
        self,
        *,
        system: str,
        prompt: str,
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.enabled:
            return fallback
        text = await self._complete(system=system, prompt=prompt, json_mode=True)
        try:
            return json.loads(_strip_fence(text))
        except (json.JSONDecodeError, TypeError) as exc:
            raise ModelUnavailableError("Model returned invalid structured output") from exc

    async def complete_text(self, *, system: str, prompt: str, fallback: str) -> str:
        if not self.enabled:
            return fallback
        return await self._complete(system=system, prompt=prompt, json_mode=False)

    async def _complete(self, *, system: str, prompt: str, json_mode: bool) -> str:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=90)
        body: dict[str, Any] = {
            "model": self.settings.openai_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            response = await client.post(
                f"{self.settings.openai_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
                json=body,
            )
            response.raise_for_status()
            return str(response.json()["choices"][0]["message"]["content"])
        except (httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
            raise ModelUnavailableError(f"Model request failed: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()


def _strip_fence(value: str) -> str:
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", value.strip(), flags=re.DOTALL)
    return match.group(1) if match else value
