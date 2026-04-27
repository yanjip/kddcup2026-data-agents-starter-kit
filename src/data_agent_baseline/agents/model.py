from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

from openai import APIError, AsyncOpenAI, BadRequestError, RateLimitError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ModelStep:
    thought: str
    action: str
    action_input: dict[str, Any]
    raw_response: str


class ModelAdapter(Protocol):
    async def complete(self, messages: list[ModelMessage]) -> str:
        raise NotImplementedError


class OpenAIModelAdapter:
    def __init__(
        self,
        *,
        model: str,
        api_base: str,
        api_key: str,
        temperature: float,
    ) -> None:
        self.model = model
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.temperature = temperature
        self._client: AsyncOpenAI | None = None

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            if not self.api_key:
                raise RuntimeError("Missing model API key in config.agent.api_key.")
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.api_base,
            )
        return self._client
    
    async def close(self) -> None:
        """Close the async client to prevent 'Event loop is closed' warnings."""
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def complete(self, messages: list[ModelMessage]) -> str:
        max_retries = 8
        last_exception = None

        for attempt in range(1, max_retries + 1):
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": message.role, "content": message.content} for message in messages],
                    extra_body={"enable_thinking": True},
                    temperature=self.temperature
                )

                choices = response.choices or []
                if not choices:
                    raise RuntimeError("Model response missing choices.")
                content = choices[0].message.content
                if not isinstance(content, str):
                    raise RuntimeError("Model response missing text content.")
                return content

            except BadRequestError as exc:
                # 400 永久错误（如 input 超长）：不重试，直接抛出
                logger.error(f"BadRequest 400 (permanent, no retry): {exc}")
                raise RuntimeError(f"Model request failed (400 BadRequest): {exc}") from exc
            except RateLimitError as exc:
                # 429 限流专用：长退避，给 API 配额恢复时间
                last_exception = exc
                if attempt < max_retries:
                    wait_time = 15 * attempt  # 15s, 30s, 45s...
                    logger.warning(f"Rate limit 429 (attempt {attempt}/{max_retries}). Waiting {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"Rate limit persisted after {max_retries} attempts: {exc}")
                    raise RuntimeError(f"Model request failed after {max_retries} attempts: {exc}") from exc
            except APIError as exc:
                last_exception = exc
                if attempt < max_retries:
                    logger.warning(f"Model request failed (attempt {attempt}/{max_retries}): {exc}. Retrying...")
                    await asyncio.sleep(2 * attempt)
                else:
                    logger.error(f"Model request failed after {max_retries} attempts: {exc}")
                    raise RuntimeError(f"Model request failed after {max_retries} attempts: {exc}") from exc
            except Exception as exc:
                last_exception = exc
                if attempt < max_retries:
                    logger.warning(f"Model request failed (attempt {attempt}/{max_retries}): {exc}. Retrying...")
                    await asyncio.sleep(2 * attempt)
                else:
                    logger.error(f"Model request failed after {max_retries} attempts: {exc}")
                    raise RuntimeError(f"Model request failed after {max_retries} attempts: {exc}") from exc


class ScriptedModelAdapter:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def complete(self, messages: list[ModelMessage]) -> str:
        del messages
        if not self._responses:
            raise RuntimeError("No scripted model responses remaining.")
        return self._responses.pop(0)
