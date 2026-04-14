from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

from openai import APIError, OpenAI

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
    def complete(self, messages: list[ModelMessage]) -> str:
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

    def complete(self, messages: list[ModelMessage]) -> str:
        if not self.api_key:
            raise RuntimeError("Missing model API key in config.agent.api_key.")

        client = OpenAI(
            api_key=self.api_key,
            base_url=self.api_base,
        )

        max_retries = 5
        last_exception = None

        for attempt in range(1, max_retries + 1):
            try:
                response = client.chat.completions.create(
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

            except APIError as exc:
                last_exception = exc
                if attempt < max_retries:
                    logger.warning(f"Model request failed (attempt {attempt}/{max_retries}): {exc}. Retrying...")
                    time.sleep(1.5 * attempt)  # 指数退避：0.5s, 1s, 1.5s, 2s, 2.5s
                else:
                    logger.error(f"Model request failed after {max_retries} attempts: {exc}")
                    raise RuntimeError(f"Model request failed after {max_retries} attempts: {exc}") from exc
            except Exception as exc:
                last_exception = exc
                if attempt < max_retries:
                    logger.warning(f"Model request failed (attempt {attempt}/{max_retries}): {exc}. Retrying...")
                    time.sleep(1.5 * attempt)
                else:
                    logger.error(f"Model request failed after {max_retries} attempts: {exc}")
                    raise RuntimeError(f"Model request failed after {max_retries} attempts: {exc}") from exc


class ScriptedModelAdapter:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    def complete(self, messages: list[ModelMessage]) -> str:
        del messages
        if not self._responses:
            raise RuntimeError("No scripted model responses remaining.")
        return self._responses.pop(0)
