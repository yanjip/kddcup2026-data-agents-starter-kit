from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from data_agent_baseline.agents.model import ModelAdapter, ModelMessage, ModelStep
from data_agent_baseline.agents.prompt import (
    REACT_SYSTEM_PROMPT,
    build_observation_prompt,
    build_system_prompt,
    build_task_prompt,
)
from data_agent_baseline.agents.runtime import AgentRuntimeState, StepRecord
from data_agent_baseline.benchmark.schema import AnswerTable, PublicTask
from data_agent_baseline.tools.registry import ToolRegistry


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


@dataclass(frozen=True, slots=True)
class SubAgentConfig:
    max_steps: int = 16
    name: str = "subagent"


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
class ForkRequest:
    task_description: str
    task_context: str
    expected_output: str


@dataclass(frozen=True, slots=True)
class ForkResult:
    subagent_name: str
    success: bool
    result: Any
    steps: list[StepRecord]
    error: str | None = None


@dataclass(slots=True)
class SubAgent:
    name: str
    model: ModelAdapter
    tools: ToolRegistry
    config: SubAgentConfig
    inherited_messages: list[ModelMessage]
    sub_task: PublicTask | None = None
    state: AgentRuntimeState = field(default_factory=AgentRuntimeState)

    def run(self, task: PublicTask) -> ForkResult:
        self.sub_task = task
        self.state = AgentRuntimeState()

        for step_index in range(1, self.config.max_steps + 1):
            raw_response = self.model.complete(self.inherited_messages + [ModelMessage(role="user", content=build_task_prompt(task))])
            for step in self.state.steps:
                self.inherited_messages.append(ModelMessage(role="assistant", content=step.raw_response))
                self.inherited_messages.append(ModelMessage(role="user", content=build_observation_prompt(step.observation)))

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
                self.state.steps.append(step_record)

                if tool_result.is_terminal:
                    self.state.answer = tool_result.answer
                    return ForkResult(
                        subagent_name=self.name,
                        success=True,
                        result=tool_result.answer,
                        steps=list(self.state.steps),
                    )
            except Exception as exc:
                observation = {
                    "ok": False,
                    "error": str(exc),
                }
                self.state.steps.append(
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
                return ForkResult(
                    subagent_name=self.name,
                    success=False,
                    result=None,
                    steps=list(self.state.steps),
                    error=str(exc),
                )

        return ForkResult(
            subagent_name=self.name,
            success=False,
            result=None,
            steps=list(self.state.steps),
            error="SubAgent did not complete within max_steps.",
        )


@dataclass(frozen=True, slots=True)
class OrchestratorConfig:
    max_main_steps: int = 8
    max_subagents: int = 3


ORCHESTRATOR_SYSTEM_PROMPT = """
You are the Orchestrator Agent responsible for coordinating complex data analysis tasks.

## Your Responsibilities

1. **Task Analysis**: Understand the overall task and break it down into manageable sub-tasks
2. **Planning**: Create a clear execution plan with independent work streams that can run in parallel
3. **Delegation**: Fork sub-agents to handle specific sub-tasks when appropriate
4. **Synthesis**: Combine results from sub-agents to produce the final answer

## Fork Mechanism

When you identify a sub-task suitable for parallel execution, you can fork a sub-agent by returning:
```json
{"thought":"...","action":"fork_subagent","action_input":{"task_description":"...","task_context":"...","expected_output":"..."}}
```

The sub-agent will inherit your complete context including:
- System prompt with tool definitions
- Full conversation history
- All tool registrations

## Core Rules

1. First, analyze the task and create a clear plan before executing any actions
2. Identify independent work streams that can be executed in parallel via sub-agents
3. Sub-agents are especially useful for:
   - Independent data exploration tasks
   - Parallel hypothesis testing
   - Multiple independent queries
   - Complex transformations that can be split
4. Maximum 3 sub-agents can be active at once
5. Always call the `answer` tool when you have the final result
6. Return exactly one JSON object with keys `thought`, `action`, and `action_input`
7. Always wrap the JSON object in exactly one fenced code block starting with ```json and ending with ```
8. Do not output any text before or after the fenced JSON block
""".strip()


ORCHESTRATOR_RESPONSE_EXAMPLES = """
Example response when forking a sub-agent:
```json
{"thought":"Plan: 1. Explore data structure in parallel using sub-agents\\n2. Combine results for final analysis\\nBlocker: None\\nAssumption: Independent data exploration can happen in parallel\\n\\nI'll fork two sub-agents to explore different aspects of the data.","action":"fork_subagent","action_input":{"task_description":"Explore the customers table schema and sample data","task_context":"List files in context/, read knowledge.md for schema info","expected_output":"Schema details and sample rows from customers table"}}
```

Example response when handling fork result:
```json
{"thought":"Sub-agent 'explorer1' completed successfully. Got schema for customers table.\\nPlan: 1. ✓ Schema exploration\\n2. Query customers table\\n3. Join with orders\\nBlocker: None\\nAssumption: None\\n\\nNow I have the schema, I'll proceed with querying.","action":"read_doc","action_input":{"path":"context/customers.csv"}}
```

Example response for final answer:
```json
{"thought":"Plan: 1. ✓ Explored data\\n2. ✓ Queried tables\\n3. ✓ Combined results\\nBlocker: None\\nAssumption: Verified\\n\\nI have the final result table.","action":"answer","action_input":{"columns":["department","avg_salary"],"rows":[["Engineering","95000"],["Sales","72000"]]}}
```
""".strip()


@dataclass(frozen=True, slots=True)
class OrchestratorAgentConfig:
    max_main_steps: int = 8
    max_subagents: int = 3


class OrchestratorAgent:
    def __init__(
        self,
        *,
        model: ModelAdapter,
        tools: ToolRegistry,
        config: OrchestratorAgentConfig | None = None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.config = config or OrchestratorAgentConfig()
        self._subagents: list[SubAgent] = []
        self._state = OrchestratorRuntimeState()

    def _build_system_prompt(self) -> str:
        tool_descriptions = self.tools.describe_for_prompt()
        return (
            f"{ORCHESTRATOR_SYSTEM_PROMPT}\n\n"
            "Available tools:\n"
            f"{tool_descriptions}\n\n"
            f"{ORCHESTRATOR_RESPONSE_EXAMPLES}\n\n"
            "You must always return a single ```json fenced block containing one JSON object "
            "with keys `thought`, `action`, and `action_input`, and no extra text."
        )

    def _build_messages(self, task: PublicTask) -> list[ModelMessage]:
        system_content = self._build_system_prompt()
        messages = [ModelMessage(role="system", content=system_content)]
        messages.append(ModelMessage(role="user", content=build_task_prompt(task)))

        for step in self._state.steps:
            messages.append(ModelMessage(role="assistant", content=step.raw_response))
            messages.append(
                ModelMessage(role="user", content=build_observation_prompt(step.observation))
            )

        if self._subagents:
            subagent_context = "\n\n## Active SubAgent Results:\n"
            for subagent in self._subagents:
                subagent_context += f"\n### SubAgent: {subagent.name}\n"
                subagent_context += f"Steps taken: {len(subagent.state.steps)}\n"
                if subagent.state.answer:
                    subagent_context += f"Result: {subagent.state.answer}\n"
                elif subagent.state.failure_reason:
                    subagent_context += f"Failed: {subagent.state.failure_reason}\n"
            messages.append(ModelMessage(role="user", content=subagent_context))

        return messages

    def _fork_subagent(self, task: PublicTask, task_description: str, task_context: str, expected_output: str) -> ForkResult:
        if len(self._subagents) >= self.config.max_subagents:
            return ForkResult(
                subagent_name="",
                success=False,
                result=None,
                steps=[],
                error=f"Maximum number of subagents ({self.config.max_subagents}) reached.",
            )

        subagent_name = f"subagent_{len(self._subagents) + 1}"
        inherited_messages = self._build_messages(task)

        subagent = SubAgent(
            name=subagent_name,
            model=self.model,
            tools=self.tools,
            config=SubAgentConfig(max_steps=12, name=subagent_name),
            inherited_messages=inherited_messages,
        )
        self._subagents.append(subagent)

        class SubTask:
            def __init__(self, desc: str, ctx: str, out: str, task_id: str):
                self.task_id = task_id
                self.question = f"{desc}\n\nContext: {ctx}\n\nExpected output: {out}"
                self.context_path = task.context_path

        sub_task = SubTask(task_description, task_context, expected_output, f"{task.task_id}_{subagent_name}")
        return subagent.run(sub_task)

    def run(self, task: PublicTask) -> OrchestratorRunResult:
        self._state = OrchestratorRuntimeState()
        self._subagents = []

        for step_index in range(1, self.config.max_main_steps + 1):
            raw_response = self.model.complete(self._build_messages(task))

            try:
                model_step = parse_model_step(raw_response)

                tool_result = self.tools.execute(task, model_step.action, model_step.action_input)
                observation = {
                    "ok": tool_result.ok,
                    "tool": model_step.action,
                    "content": tool_result.content,
                }

                if model_step.action == "fork_subagent":
                    task_desc = model_step.action_input.get("task_description", "")
                    task_ctx = model_step.action_input.get("task_context", "")
                    expected_out = model_step.action_input.get("expected_output", "")
                    fork_result = self._fork_subagent(task, task_desc, task_ctx, expected_out)

                    observation = {
                        "ok": fork_result.success,
                        "tool": "fork_subagent",
                        "subagent_name": fork_result.subagent_name,
                        "result": str(fork_result.result) if fork_result.result else None,
                        "error": fork_result.error,
                        "steps": [s.to_dict() for s in fork_result.steps],
                    }

                step_record = OrchestratorStepRecord(
                    step_index=step_index,
                    thought=model_step.thought,
                    action=model_step.action,
                    action_input=model_step.action_input,
                    raw_response=raw_response,
                    observation=observation,
                    ok=tool_result.ok,
                )
                self._state.steps.append(step_record)

                if tool_result.is_terminal:
                    self._state.answer = tool_result.answer
                    break

            except Exception as exc:
                observation = {
                    "ok": False,
                    "error": str(exc),
                }
                self._state.steps.append(
                    OrchestratorStepRecord(
                        step_index=step_index,
                        thought="",
                        action="__error__",
                        action_input={},
                        raw_response=raw_response,
                        observation=observation,
                        ok=False,
                    )
                )

        all_steps: list[OrchestratorStepRecord] = list(self._state.steps)
        for subagent in self._subagents:
            for step in subagent.state.steps:
                all_steps.append(
                    OrchestratorStepRecord(
                        step_index=step.step_index,
                        thought=step.thought,
                        action=step.action,
                        action_input=step.action_input,
                        raw_response=step.raw_response,
                        observation=step.observation,
                        ok=step.ok,
                        subagent_name=subagent.name,
                    )
                )

        if self._state.answer is None and self._state.failure_reason is None:
            self._state.failure_reason = "Orchestrator did not submit an answer within max_steps."

        return OrchestratorRunResult(
            task_id=task.task_id,
            answer=self._state.answer,
            steps=all_steps,
            failure_reason=self._state.failure_reason,
            subagent_count=len(self._subagents),
        )


@dataclass(slots=True)
class OrchestratorRuntimeState:
    steps: list[OrchestratorStepRecord] | None = None
    answer: AnswerTable | None = None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        if self.steps is None:
            self.steps = []


@dataclass(frozen=True, slots=True)
class OrchestratorStepRecord:
    step_index: int
    thought: str
    action: str
    action_input: dict[str, Any]
    raw_response: str
    observation: dict[str, Any]
    ok: bool
    subagent_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "step_index": self.step_index,
            "thought": self.thought,
            "action": self.action,
            "action_input": self.action_input,
            "raw_response": self.raw_response,
            "observation": self.observation,
            "ok": self.ok,
        }
        if self.subagent_name:
            result["subagent_name"] = self.subagent_name
        return result


@dataclass(frozen=True, slots=True)
class OrchestratorRunResult:
    task_id: str
    answer: AnswerTable | None
    steps: list[OrchestratorStepRecord]
    failure_reason: str | None
    subagent_count: int

    @property
    def succeeded(self) -> bool:
        return self.answer is not None and self.failure_reason is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "answer": self.answer.to_dict() if self.answer is not None else None,
            "steps": [step.to_dict() for step in self.steps],
            "failure_reason": self.failure_reason,
            "succeeded": self.succeeded,
            "subagent_count": self.subagent_count,
        }
