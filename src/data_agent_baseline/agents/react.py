from __future__ import annotations

import json
import re
from dataclasses import dataclass

from data_agent_baseline.agents.model import ModelAdapter, ModelMessage, ModelStep
from data_agent_baseline.agents.prompt import (
    REACT_SYSTEM_PROMPT,
    build_observation_prompt,
    build_system_prompt,
    build_task_prompt,
)
from data_agent_baseline.agents.runtime import AgentRunResult, AgentRuntimeState, StepRecord
from data_agent_baseline.benchmark.schema import PublicTask
from data_agent_baseline.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class ReActAgentConfig:
    max_steps: int = 16


def _strip_json_fence(raw_response: str) -> str:
    text = raw_response.strip()
    fence_match = re.search(r"```json\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fence_match is not None:
        return fence_match.group(1).strip()
    generic_fence_match = re.search(r"```\s*(.*?)\s*```", text, flags=re.DOTALL)
    if generic_fence_match is not None:
        return generic_fence_match.group(1).strip()
    return text


def _load_single_json_object(text: str) -> dict[str, object]:
    payload, end = json.JSONDecoder().raw_decode(text)
    remainder = text[end:].strip()
    if remainder:
        cleaned_remainder = re.sub(r"(?:\\[nrt])+", "", remainder).strip()
        if cleaned_remainder:
            raise ValueError("Model response must contain only one JSON object.")
    if not isinstance(payload, dict):
        raise ValueError("Model response must be a JSON object.")
    return payload


def parse_model_step(raw_response: str) -> ModelStep:
    normalized = _strip_json_fence(raw_response)
    payload = _load_single_json_object(normalized)

    thought = payload.get("thought", "")
    action = payload.get("action")
    action_input = payload.get("action_input", {})
    if not isinstance(thought, str):
        raise ValueError("thought must be a string.")
    if not isinstance(action, str) or not action:
        raise ValueError("action must be a non-empty string.")
    if not isinstance(action_input, dict):
        raise ValueError("action_input must be a JSON object.")

    return ModelStep(
        thought=thought,
        action=action,
        action_input=action_input,
        raw_response=raw_response,
    )


@dataclass(frozen=True, slots=True)
class SchemaKnowledge:
    tables: dict[str, list[str]]
    semantic_mappings: dict[str, str]

class ReActAgent:
    def __init__(
        self,
        *,
        model: ModelAdapter,
        tools: ToolRegistry,
        config: ReActAgentConfig | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.config = config or ReActAgentConfig()
        self.system_prompt = system_prompt or REACT_SYSTEM_PROMPT

    def _auto_load_schema_knowledge(self, task: PublicTask) -> SchemaKnowledge | None:
        try:
            list_result = self.tools.execute(task, "list_context", {"max_depth": 4})
            if not list_result.ok:
                return None
            
            entries = list_result.content.get("entries", [])
            knowledge_path = None
            for entry in entries:
                if entry.get("kind") == "file" and entry.get("path", "").endswith("knowledge.md"):
                    knowledge_path = entry["path"]
                    break
            
            if not knowledge_path:
                return None
            
            read_result = self.tools.execute(task, "read_doc", {"path": knowledge_path})
            if not read_result.ok:
                return None
            
            content = read_result.content.get("preview", "")
            tables = self._parse_tables_from_knowledge(content)
            semantic_mappings = self._parse_semantic_mappings_from_knowledge(content)
            
            return SchemaKnowledge(tables=tables, semantic_mappings=semantic_mappings)
        except Exception:
            return None

    def _parse_tables_from_knowledge(self, content: str) -> dict[str, list[str]]:
        tables = {}
        lines = content.split('\n')
        current_table = None
        
        for line in lines:
            line = line.strip()
            if line.startswith('### '):
                current_table = line[4:].strip()
                tables[current_table] = []
            elif current_table and line.startswith('- **'):
                column_match = line.split('**')[1]
                if ':' in column_match:
                    column_name = column_match.split(':')[0].strip()
                    tables[current_table].append(column_name)
        
        return tables

    def _parse_semantic_mappings_from_knowledge(self, content: str) -> dict[str, str]:
        mappings = {}
        lines = content.split('\n')
        
        for line in lines:
            line = line.strip()
            if ':**' in line and ('**' in line):
                parts = line.split(':**')
                if len(parts) == 2:
                    key = parts[0].strip().replace('**', '')
                    value = parts[1].strip().split('**')[0]
                    mappings[key] = value
        
        return mappings

    def _build_messages(self, task: PublicTask, state: AgentRuntimeState, schema_knowledge: SchemaKnowledge | None) -> list[ModelMessage]:
        system_content = build_system_prompt(
            self.tools.describe_for_prompt(),
            system_prompt=self.system_prompt,
        )
        
        if schema_knowledge:
            schema_info = "\n\nSchema Knowledge:\n"
            schema_info += "Tables and columns:\n"
            for table, columns in schema_knowledge.tables.items():
                schema_info += f"- {table}: {', '.join(columns)}\n"
            schema_info += "\nSemantic mappings:\n"
            for key, value in schema_knowledge.semantic_mappings.items():
                schema_info += f"- {key}: {value}\n"
            system_content += schema_info
        
        messages = [ModelMessage(role="system", content=system_content)]
        messages.append(ModelMessage(role="user", content=build_task_prompt(task)))
        for step in state.steps:
            messages.append(ModelMessage(role="assistant", content=step.raw_response))
            messages.append(
                ModelMessage(role="user", content=build_observation_prompt(step.observation))
            )
        return messages

    def run(self, task: PublicTask) -> AgentRunResult:
        schema_knowledge = self._auto_load_schema_knowledge(task)
        state = AgentRuntimeState()
        
        for step_index in range(1, self.config.max_steps + 1):
            raw_response = self.model.complete(self._build_messages(task, state, schema_knowledge))
            try:
                model_step = parse_model_step(raw_response)
                tool_result = self.tools.execute(task, model_step.action, model_step.action_input)
                observation = {
                    "ok": tool_result.ok,
                    "tool": model_step.action,
                    "content": tool_result.content,
                }
                step_record = StepRecord(
                    step_index=step_index,
                    thought=model_step.thought,
                    action=model_step.action,
                    action_input=model_step.action_input,
                    raw_response=raw_response,
                    observation=observation,
                    ok=tool_result.ok,
                )
                state.steps.append(step_record)
                if tool_result.is_terminal:
                    state.answer = tool_result.answer
                    break
            except Exception as exc:
                observation = {
                    "ok": False,
                    "error": str(exc),
                }
                state.steps.append(
                    StepRecord(
                        step_index=step_index,
                        thought="",
                        action="__error__",
                        action_input={},
                        raw_response=raw_response,
                        observation=observation,
                        ok=False,
                    )
                )

        if state.answer is None and state.failure_reason is None:
            state.failure_reason = "Agent did not submit an answer within max_steps."

        return AgentRunResult(
            task_id=task.task_id,
            answer=state.answer,
            steps=list(state.steps),
            failure_reason=state.failure_reason,
        )
