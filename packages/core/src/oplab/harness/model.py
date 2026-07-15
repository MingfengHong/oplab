from __future__ import annotations

import json
import re
from asyncio import sleep
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from oplab.config import Settings


class ModelUnavailableError(RuntimeError):
    pass


ModelT = TypeVar("ModelT", bound=BaseModel)


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

    async def complete_model(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[ModelT],
        fallback: ModelT,
        attempts: int = 3,
    ) -> ModelT:
        """Request and validate a typed decision, feeding validation errors back to the model."""
        if not self.enabled:
            return fallback
        schema_text = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        current_prompt = f"{prompt}\n\nReturn JSON matching this schema exactly:\n{schema_text}"
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                text = await self._complete(system=system, prompt=current_prompt, json_mode=True)
                return schema.model_validate_json(_strip_fence(text))
            except (ValidationError, json.JSONDecodeError, ModelUnavailableError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    current_prompt = (
                        f"{prompt}\n\nYour previous output failed validation: {exc}. "
                        f"Return only corrected JSON matching this schema:\n{schema_text}"
                    )
        raise ModelUnavailableError(f"Model failed typed output validation: {last_error}")

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
            last_error: Exception | None = None
            for attempt in range(3):
                try:
                    response = await client.post(
                        f"{self.settings.openai_base_url.rstrip('/')}/chat/completions",
                        headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
                        json=body,
                    )
                    response.raise_for_status()
                    return str(response.json()["choices"][0]["message"]["content"])
                except (httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
                    last_error = exc
                    retryable = isinstance(exc, (httpx.TransportError, httpx.TimeoutException)) or (
                        isinstance(exc, httpx.HTTPStatusError)
                        and exc.response.status_code in {408, 409, 429, 500, 502, 503, 504}
                    )
                    if not retryable or attempt == 2:
                        break
                    await sleep(0.5 * (2**attempt))
            raise ModelUnavailableError(f"Model request failed: {last_error}") from last_error
        finally:
            if owns_client:
                await client.aclose()


def _strip_fence(value: str) -> str:
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", value.strip(), flags=re.DOTALL)
    return match.group(1) if match else value
