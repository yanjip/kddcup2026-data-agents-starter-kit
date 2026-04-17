from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from data_agent_baseline.agents.model import ModelAdapter, ModelMessage
from data_agent_baseline.agents.parser import parse_model_step
from data_agent_baseline.agents.prompt import (
    ORCHESTRATOR_RESPONSE_EXAMPLES,
    build_orchestrator_system_prompt,
    build_observation_prompt,
    build_task_prompt,
)
from data_agent_baseline.agents.runtime import StepRecord
from data_agent_baseline.agents.subagent import ForkRequest, ForkResult, SubAgent, SubAgentConfig
from data_agent_baseline.tools.registry import ToolRegistry, create_default_tool_registry

from data_agent_baseline.benchmark.schema import AnswerTable, PublicTask

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OrchestratorAgentConfig:
    max_main_steps: int
    max_subagent_steps: int
    max_subagents: int


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


@dataclass(slots=True)
class OrchestratorRuntimeState:
    steps: list[OrchestratorStepRecord] | None = None
    answer: AnswerTable | None = None
    failure_reason: str | None = None
    verification_done: bool = False  # 已废弃，保留用于兼容性

    def __post_init__(self) -> None:
        if self.steps is None:
            self.steps = []


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


class OrchestratorAgent:
    def __init__(
        self,
        *,
        model: ModelAdapter,
        tools: ToolRegistry,
        config: OrchestratorAgentConfig,
    ) -> None:
        self.model = model
        self.tools = tools
        self.config = config
        self._subagents: list[SubAgent] = []
        self._state = OrchestratorRuntimeState()
        self._pending_subagent_requests: list[ForkRequest] = []  # 待执行的 subagent 请求
        self._subagent_results: dict[str, ForkResult] = {}  # subagent 执行结果

    def _build_system_prompt(self, task: PublicTask) -> str:
        remaining_slots = self.config.max_subagents - len(self._subagents)
        tool_descriptions = self._filter_tool_descriptions(self.tools.describe_for_prompt(), remaining_slots)
        system_prompt = build_orchestrator_system_prompt(self.config.max_subagents, task.difficulty)
        
        if remaining_slots <= 0:
            system_prompt += f"\n\n⚠️ IMPORTANT: You have used all {self.config.max_subagents} subagents. fork_subagent is NO LONGER AVAILABLE."
        
        return (
            f"{system_prompt}\n\n"
            "Available tools:\n"
            f"{tool_descriptions}\n\n"
            f"{ORCHESTRATOR_RESPONSE_EXAMPLES}\n\n"
            "Return a single ```json fenced block with keys `thought`, `action`, `action_input`."
        )

    def _filter_tool_descriptions(self, descriptions: str, remaining_slots: int) -> str:
        """当 subagent 槽位已满时，过滤掉 fork_subagent 工具描述"""
        if remaining_slots > 0:
            return descriptions
        
        lines = descriptions.split("\n")
        filtered = []
        skip = False
        for line in lines:
            if "fork_subagent" in line and line.strip().startswith("-"):
                skip = True
                continue
            if skip and line.strip().startswith("-") and "fork_subagent" not in line:
                skip = False
            if not skip:
                filtered.append(line)
        return "\n".join(filtered)

    def _build_messages(self, task: PublicTask) -> list[ModelMessage]:
        system_content = self._build_system_prompt(task)
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

    async def _create_subagent(self, task: PublicTask, fork_request: ForkRequest, subagent_name: str) -> ForkResult:
        """创建并运行单个 subagent（异步执行）"""
        inherited_messages = self._build_messages(task)

        # SubAgent 不应该能 fork 其他 subagent，创建不包含 fork_subagent 的工具注册表
        subagent_tools = create_default_tool_registry(include_fork_subagent=False)

        subagent = SubAgent(
            name=subagent_name,
            model=self.model,
            tools=subagent_tools,
            config=SubAgentConfig(max_steps=self.config.max_subagent_steps, name=subagent_name),
            inherited_messages=inherited_messages,
        )
        self._subagents.append(subagent)

        # 使用 SimpleNamespace 创建轻量级任务对象
        from types import SimpleNamespace
        sub_task = SimpleNamespace(
            task_id=f"{task.task_id}_{subagent_name}",
            question=f"{fork_request.task_description}\n\nContext: {fork_request.task_context}\n\nExpected output: {fork_request.expected_output}",
            context_dir=task.context_dir,
            difficulty=task.difficulty,
        )
        return await subagent.run(sub_task)

    async def _execute_pending_subagents_parallel(self, task: PublicTask) -> list[ForkResult]:
        """并行执行所有待处理的 subagent 请求（使用 asyncio）"""
        if not self._pending_subagent_requests:
            return []

        remaining_slots = self.config.max_subagents - len(self._subagents)

        if remaining_slots <= 0:
            logger.warning("No remaining subagent slots available")
            return [
                ForkResult(
                    subagent_name="",
                    success=False,
                    result=None,
                    steps=[],
                    error=f"Maximum number of subagents ({self.config.max_subagents}) reached.",
                )
                for _ in self._pending_subagent_requests
            ]

        # 限制并发数量
        requests_to_process = self._pending_subagent_requests[:remaining_slots]
        self._pending_subagent_requests = self._pending_subagent_requests[remaining_slots:]

        logger.info(f"Executing {len(requests_to_process)} subagents in parallel")

        # 使用 asyncio.gather 并行执行
        tasks = []
        for i, fork_request in enumerate(requests_to_process):
            subagent_name = f"subagent_{len(self._subagents) + i + 1}"
            task_coro = self._create_subagent(task, fork_request, subagent_name)
            tasks.append(task_coro)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 处理结果
        processed_results = []
        for i, result in enumerate(results):
            subagent_name = f"subagent_{len(self._subagents) - len(results) + i + 1}"
            if isinstance(result, Exception):
                logger.error(f"Subagent {subagent_name} failed with exception: {result}")
                error_result = ForkResult(
                    subagent_name=subagent_name,
                    success=False,
                    result=None,
                    steps=[],
                    error=str(result),
                )
                processed_results.append(error_result)
                self._subagent_results[subagent_name] = error_result
            else:
                processed_results.append(result)
                self._subagent_results[subagent_name] = result
                logger.info(f"Subagent {subagent_name} completed: success={result.success}")

        return processed_results

    async def run(self, task: PublicTask) -> OrchestratorRunResult:
        self._state = OrchestratorRuntimeState()
        self._subagents = []
        
        # Track if fork_subagent has been used (for hard/extreme tasks)
        is_hard_or_extreme = task.difficulty.lower() in ("hard", "extreme")
        fork_subagent_used = False

        for step_index in range(1, self.config.max_main_steps + 1):
            # Add urgency reminder when approaching max steps (last 5 steps)
            remaining_steps = self.config.max_main_steps - step_index + 1
            if remaining_steps <= 8:
                urgency_message = f"\n\n URGENT: You have only {remaining_steps} step(s) remaining out of {self.config.max_main_steps} total steps. You MUST submit your final answer using the 'answer' tool in the next step(s). If you do not submit an answer within the remaining steps, the task will fail."
                messages = self._build_messages(task)
                messages.append(ModelMessage(role="user", content=urgency_message))
            else:
                messages = self._build_messages(task)
            
            # 调用模型，捕获可能的连接错误
            try:
                raw_response = await self.model.complete(messages)
            except Exception as model_exc:
                logger.error(f"Model request failed at step {step_index}: {model_exc}")
                error_observation = {
                    "ok": False,
                    "error": f"Model request failed: {model_exc}",
                    "message": "The model service is currently unavailable. Please try again or submit an answer with available information.",
                }
                step_record = OrchestratorStepRecord(
                    step_index=step_index,
                    thought="",
                    action="error",
                    action_input={},
                    raw_response="",
                    observation=error_observation,
                    ok=False,
                )
                self._state.steps.append(step_record)
                # 如果是最后一步，返回失败结果
                if step_index >= self.config.max_main_steps:
                    return OrchestratorRunResult(
                        task_id=task.task_id,
                        answer=None,
                        steps=list(self._state.steps),
                        failure_reason=f"Model request failed: {model_exc}",
                        subagent_count=len(self._subagents),
                    )
                # 否则继续下一步
                continue

            try:
                model_step = parse_model_step(raw_response)

                # Handle fork_subagent action internally (not through tool registry)
                if model_step.action == "fork_subagent":
                    fork_subagent_used = True
                    task_desc = model_step.action_input.get("task_description", "")
                    task_ctx = model_step.action_input.get("task_context", "")
                    expected_out = model_step.action_input.get("expected_output", "")

                    # 检查是否还有剩余的 subagent 槽位
                    remaining_slots = self.config.max_subagents - len(self._subagents)
                    
                    if remaining_slots <= 0:
                        # 槽位已满，无法创建新 subagent
                        observation = {
                            "ok": False,
                            "tool": "fork_subagent",
                            "status": "failed",
                            "message": f"Maximum number of subagents ({self.config.max_subagents}) reached. Cannot create more subagents.",
                        }
                        step_record = OrchestratorStepRecord(
                            step_index=step_index,
                            thought=model_step.thought,
                            action=model_step.action,
                            action_input=model_step.action_input,
                            raw_response=raw_response,
                            observation=observation,
                            ok=False,
                        )
                        self._state.steps.append(step_record)
                        
                        # 向 LLM 发送明确的提示
                        failure_message = (
                            f"\n\n⚠️ SubAgent limit reached: You have used all {self.config.max_subagents} subagents. "
                            f"You CANNOT fork more subagents. Please use other tools (execute_python, execute_sql, etc.) "
                            f"to complete the task or submit your answer with current information."
                        )
                        messages.append(ModelMessage(role="user", content=failure_message))
                        continue

                    # 收集 subagent 请求，稍后批量并行执行
                    fork_request = ForkRequest(
                        task_description=task_desc,
                        task_context=task_ctx,
                        expected_output=expected_out
                    )
                    self._pending_subagent_requests.append(fork_request)
                    logger.info(f"Queued subagent request: {task_desc[:50]}...")

                    # 返回排队状态，让 LLM 可以继续 fork 更多 subagent
                    observation = {
                        "ok": True,
                        "tool": "fork_subagent",
                        "status": "queued",
                        "message": f"Subagent request queued. Total pending: {len(self._pending_subagent_requests)}. Used {len(self._subagents)}/{self.config.max_subagents} subagents.",
                    }

                    step_record = OrchestratorStepRecord(
                        step_index=step_index,
                        thought=model_step.thought,
                        action=model_step.action,
                        action_input=model_step.action_input,
                        raw_response=raw_response,
                        observation=observation,
                        ok=True,
                    )
                    self._state.steps.append(step_record)
                    continue
                
                # Runtime enforcement: hard/extreme tasks MUST use fork_subagent
                if is_hard_or_extreme and not fork_subagent_used and step_index >= 3:
                    # Force subagent usage by injecting a reminder and re-trying
                    logger.warning(f"Hard/extreme task not using fork_subagent at step {step_index}. Injecting mandatory reminder.")
                    
                    # Add a forced reminder to the messages
                    forced_reminder = (
                        "\n\n⚠️ CRITICAL REMINDER: This is a HARD/EXTREME difficulty task. "
                        "You MUST use fork_subagent to delegate sub-tasks. "
                        "You have NOT used any subagent yet. "
                        "Please fork at least one subagent NOW to proceed with parallel processing. "
                        "Do NOT attempt to solve this entirely by yourself."
                    )
                    messages.append(ModelMessage(role="user", content=forced_reminder))
                    
                    # Re-generate response with the reminder
                    raw_response = await self.model.complete(messages)
                    try:
                        model_step = parse_model_step(raw_response)
                        if model_step.action == "fork_subagent":
                            fork_subagent_used = True
                            # Process the fork_subagent action
                            task_desc = model_step.action_input.get("task_description", "")
                            task_ctx = model_step.action_input.get("task_context", "")
                            expected_out = model_step.action_input.get("expected_output", "")
                            
                            fork_request = ForkRequest(
                                task_description=task_desc,
                                task_context=task_ctx,
                                expected_output=expected_out
                            )
                            self._pending_subagent_requests.append(fork_request)
                            logger.info(f"Queued subagent request after reminder: {task_desc[:50]}...")
                            
                            observation = {
                                "ok": True,
                                "tool": "fork_subagent",
                                "status": "queued",
                                "message": f"Subagent request queued. Total pending: {len(self._pending_subagent_requests)}",
                            }
                            
                            step_record = OrchestratorStepRecord(
                                step_index=step_index,
                                thought=model_step.thought,
                                action=model_step.action,
                                action_input=model_step.action_input,
                                raw_response=raw_response,
                                observation=observation,
                                ok=True,
                            )
                            self._state.steps.append(step_record)
                            continue
                    except Exception as e:
                        logger.error(f"Failed to parse model response after reminder: {e}")

                # 如果有待执行的 subagent 请求，先并行执行它们
                if self._pending_subagent_requests and model_step.action != "fork_subagent":
                    logger.info(f"Executing {len(self._pending_subagent_requests)} pending subagents before {model_step.action}")
                    parallel_results = await self._execute_pending_subagents_parallel(task)

                    # 添加 subagent 执行结果到观察
                    subagent_observation = {
                        "ok": all(r.success for r in parallel_results),
                        "tool": "parallel_subagents_completed",
                        "subagent_count": len(parallel_results),
                        "successful_count": sum(1 for r in parallel_results if r.success),
                        "results": [
                            {
                                "subagent_name": r.subagent_name,
                                "success": r.success,
                                "result": str(r.result) if r.result else None,
                                "error": r.error,
                            }
                            for r in parallel_results
                        ],
                    }
                    self._state.steps.append(
                        OrchestratorStepRecord(
                            step_index=step_index,
                            thought=f"[PARALLEL SUBAGENTS] Executed {len(parallel_results)} subagents in parallel",
                            action="parallel_subagents_completed",
                            action_input={},
                            raw_response=f'```json\n{{"thought": "Parallel subagents completed execution.", "action": "parallel_subagents_completed", "action_input": {{}}}}\n```',
                            observation=subagent_observation,
                            ok=subagent_observation["ok"],
                        )
                    )
                    
                    # 如果 subagent 执行失败，给出明确提示
                    if not subagent_observation["ok"]:
                        failure_details = []
                        for r in parallel_results:
                            if not r.success and r.error:
                                failure_details.append(f"{r.subagent_name}: {r.error}")
                        if failure_details:
                            failure_message = (
                                f"\n\n⚠️ Some SubAgents failed:\n" + "\n".join(failure_details) +
                                f"\n\nYou have used {len(self._subagents)}/{self.config.max_subagents} subagents. "
                                f"Please use other tools to complete the task or submit your answer with current information."
                            )
                            messages.append(ModelMessage(role="user", content=failure_message))

                tool_result = self.tools.execute(task, model_step.action, model_step.action_input)
                observation = {
                    "ok": tool_result.ok,
                    "tool": model_step.action,
                    "content": tool_result.content,
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
                    # 答案提交，直接接受
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
