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
from data_agent_baseline.tools.registry import create_default_tool_registry
from data_agent_baseline.agents.verification_agent import (
    VerificationAgent,
    VerificationAgentConfig,
    VerificationResult,
    should_verify_answer,
)
from data_agent_baseline.benchmark.schema import AnswerTable, PublicTask
from data_agent_baseline.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OrchestratorConfig:
    max_main_steps: int = 8
    max_subagents: int = 3


@dataclass(frozen=True, slots=True)
class OrchestratorAgentConfig:
    max_main_steps: int
    max_subagent_steps: int
    max_subagents: int
    enable_verification: bool = False  # 是否启用验证 Agent


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
    verification_done: bool = False  # 标记是否已完成验证（保留用于兼容性）
    verification_attempt_count: int = 0  # 验证尝试次数（替代 verification_done）
    verification_result: VerificationResult | None = None  # 验证结果

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
        self._enable_verification = config.enable_verification
        self._subagents: list[SubAgent] = []
        self._state = OrchestratorRuntimeState()
        self._verifier: VerificationAgent | None = None
        self._pending_subagent_requests: list[ForkRequest] = []  # 待执行的 subagent 请求
        self._subagent_results: dict[str, ForkResult] = {}  # subagent 执行结果

    def _build_system_prompt(self, task: PublicTask) -> str:
        tool_descriptions = self.tools.describe_for_prompt()
        system_prompt = build_orchestrator_system_prompt(self.config.max_subagents, task.difficulty)
        return (
            f"{system_prompt}\n\n"
            "Available tools:\n"
            f"{tool_descriptions}\n\n"
            f"{ORCHESTRATOR_RESPONSE_EXAMPLES}\n\n"
            "You must always return a single ```json fenced block containing one JSON object "
            "with keys `thought`, `action`, and `action_input`, and no extra text."
        )

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

    def _filter_messages_for_subagent(self, messages: list[ModelMessage]) -> list[ModelMessage]:
        """过滤消息列表，移除会让 SubAgent 误认自己是主 agent 的内容。

        具体移除：
        1. Orchestrator 的 system prompt（SubAgent 有自己的 system prompt）
        2. 所有包含 fork_subagent 调用的历史记录（assistant + 对应 observation）
        """
        filtered: list[ModelMessage] = []
        i = 0
        while i < len(messages):
            msg = messages[i]
            # 跳过 Orchestrator 的 system prompt
            if msg.role == "system":
                i += 1
                continue

            # 如果 assistant 消息包含 fork_subagent，跳过它及对应的 user observation
            if msg.role == "assistant" and "fork_subagent" in msg.content:
                i += 1
                # 跳过对应的 observation（下一个 user 消息）
                if i < len(messages) and messages[i].role == "user":
                    i += 1
                continue

            filtered.append(msg)
            i += 1

        return filtered

    async def _create_subagent(self, task: PublicTask, fork_request: ForkRequest, subagent_name: str) -> ForkResult:
        """创建并运行单个 subagent（异步执行）"""
        inherited_messages = self._build_messages(task)
        inherited_messages = self._filter_messages_for_subagent(inherited_messages)

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

        class SubTask:
            def __init__(self, desc: str, ctx: str, out: str, task_id: str, context_dir):
                self.task_id = task_id
                self.question = f"{desc}\n\nContext: {ctx}\n\nExpected output: {out}"
                self.context_dir = context_dir
                self.difficulty = task.difficulty  # 继承原任务难度

        sub_task = SubTask(
            fork_request.task_description,
            fork_request.task_context,
            fork_request.expected_output,
            f"{task.task_id}_{subagent_name}",
            task.context_dir
        )
        try:
            result = await subagent.run(sub_task)
        finally:
            self._subagents.remove(subagent)
        return result

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

    async def _verify_answer(
        self, 
        task: PublicTask, 
        proposed_answer: AnswerTable,
        original_thought: str = ""
    ) -> VerificationResult | None:
        """
        验证答案
        
        Args:
            task: 原始任务
            proposed_answer: 候选答案
            original_thought: 原始推理过程
            
        Returns:
            VerificationResult | None: 
                - VerificationResult: 验证完成，包含验证结果
                - None: 验证执行失败（如工具错误等），无法完成验证
        """
        # 初始化验证Agent（如果未初始化）
        if self._verifier is None:
            self._verifier = VerificationAgent(
                name="verifier",
                model=self.model,
                tools=self.tools,
                config=VerificationAgentConfig(max_steps=10)
            )
        
        # 构建执行历史，帮助验证 Agent 了解主 Agent 的数据源和推理路径
        execution_history = []
        for step in self._state.steps:
            execution_history.append({
                "step_index": step.step_index,
                "thought": step.thought,
                "action": step.action,
                "action_input": step.action_input,
                "ok": step.ok,
            })
        
        # 执行验证
        verification_result = await self._verifier.run(
            task=task,
            proposed_answer=proposed_answer,
            original_reasoning=original_thought,
            execution_history=execution_history
        )
        
        # 如果验证执行失败（返回 None），直接返回 None
        if verification_result is None:
            return None
        
        # 保存验证结果
        self._state.verification_result = verification_result
        
        return verification_result

    async def run(self, task: PublicTask) -> OrchestratorRunResult:
        self._state = OrchestratorRuntimeState()
        self._subagents = []

        for step_index in range(1, self.config.max_main_steps + 1):
            # Add urgency reminder when approaching max steps (last 5 steps)
            remaining_steps = self.config.max_main_steps - step_index + 1
            if remaining_steps <= 8:
                urgency_message = f"\n\n URGENT: You have only {remaining_steps} step(s) remaining out of {self.config.max_main_steps} total steps. You MUST submit your final answer using the 'answer' tool in the next step(s). If you do not submit an answer within the remaining steps, the task will fail."
                messages = self._build_messages(task)
                messages.append(ModelMessage(role="user", content=urgency_message))
            else:
                messages = self._build_messages(task)
            raw_response = await self.model.complete(messages)

            try:
                model_step = parse_model_step(raw_response)

                # Handle fork_subagent action internally (not through tool registry)
                if model_step.action == "fork_subagent":
                    task_desc = model_step.action_input.get("task_description", "")
                    task_ctx = model_step.action_input.get("task_context", "")
                    expected_out = model_step.action_input.get("expected_output", "")

                    # 收集 subagent 请求，稍后批量并行执行
                    fork_request = ForkRequest(
                        task_description=task_desc,
                        task_context=task_ctx,
                        expected_output=expected_out
                    )
                    self._pending_subagent_requests.append(fork_request)
                    logger.info(f"Queued subagent request: {task_desc[:50]}...")

                    is_knowledge_reader = "knowledge.md" in task_desc.lower()

                    if is_knowledge_reader:
                        observation = {
                            "ok": True,
                            "tool": "fork_subagent",
                            "status": "executing",
                            "message": "Knowledge.md reader subagent is executing. Results will be available in the next step.",
                        }
                    else:
                        # 立即返回一个临时观察，让 LLM 继续发送更多 fork 请求
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

                    # 如果是 knowledge.md reader，立即执行并等待结果
                    if is_knowledge_reader and self._pending_subagent_requests:
                        logger.info("Executing knowledge.md reader immediately")
                        parallel_results = await self._execute_pending_subagents_parallel(task)

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
                                raw_response="",
                                observation=subagent_observation,
                                ok=subagent_observation["ok"],
                            )
                        )

                    continue

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
                            raw_response="",
                            observation=subagent_observation,
                            ok=subagent_observation["ok"],
                        )
                    )

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
                    # 答案提交，进行验证（如果启用且未超过最大验证次数）
                    max_verification_attempts = 3  # 最多验证3次（防止死循环）
                    should_run_verification = (
                        self._enable_verification 
                        and tool_result.answer
                        and should_verify_answer(task, tool_result.answer)
                        and self._state.verification_attempt_count < max_verification_attempts
                    )
                    
                    if should_run_verification:
                        self._state.verification_attempt_count += 1
                        logger.info(f"Running verification attempt {self._state.verification_attempt_count}/{max_verification_attempts}")
                        
                        verification_result = await self._verify_answer(task, tool_result.answer, model_step.thought)
                        
                        # 处理验证结果
                        if verification_result is None:
                            # 验证执行失败（如工具错误、异常等），无法完成验证
                            # 这种情况下接受答案，因为验证本身出了问题
                            logger.warning(f"Verification could not be completed (attempt {self._state.verification_attempt_count}). Accepting answer without verification.")
                            self._state.answer = tool_result.answer
                            break
                        elif verification_result.is_valid and verification_result.confidence >= 0.7:
                            # 验证通过，接受答案
                            logger.info("Verification passed! Accepting answer.")
                            self._state.answer = tool_result.answer
                            break
                        else:
                            # 验证判断答案错误，拒绝答案并继续推理
                            logger.info(f"Verification failed. Reason: {verification_result.reasoning}")
                            
                            # 构建详细的验证失败反馈
                            failure_reason = verification_result.reasoning
                            suggested_fix = verification_result.suggested_fix
                            confidence = verification_result.confidence
                            
                            # 根据置信度调整反馈策略
                            if confidence >= 0.5:
                                # 置信度中等，可能是计算错误，建议重新检查
                                feedback = (
                                    f"Your answer was verified and found LIKELY INCORRECT (confidence: {confidence:.2f}).\n\n"
                                    f"Reason: {failure_reason}\n\n"
                                )
                            else:
                                # 置信度低，可能是数据源或方法错误
                                feedback = (
                                    f"Your answer was verified and found INCORRECT (confidence: {confidence:.2f}).\n\n"
                                    f"Reason: {failure_reason}\n\n"
                                )
                            
                            if suggested_fix:
                                feedback += f"Suggested fix: {suggested_fix}\n\n"
                            
                            remaining_attempts = max_verification_attempts - self._state.verification_attempt_count
                            if remaining_attempts > 0:
                                feedback += (
                                    "Please reconsider your approach and provide a corrected answer. "
                                    f"You have {remaining_attempts} verification attempt(s) remaining."
                                )
                            else:
                                feedback += (
                                    "No more verification attempts remaining. Please provide your best answer."
                                )
                            
                            verification_failed_observation = {
                                "ok": False,
                                "tool": "verification",
                                "content": {
                                    "status": "rejected",
                                    "reason": failure_reason,
                                    "suggested_fix": suggested_fix,
                                    "confidence": confidence,
                                    "attempt": self._state.verification_attempt_count,
                                    "max_attempts": max_verification_attempts,
                                    "detailed_feedback": feedback,
                                },
                            }
                            
                            step_record = OrchestratorStepRecord(
                                step_index=step_index,
                                thought=f"[VERIFICATION FAILED - Attempt {self._state.verification_attempt_count}/{max_verification_attempts}] {model_step.thought}",
                                action="verification_failed",
                                action_input={},
                                raw_response="",
                                observation=verification_failed_observation,
                                ok=False,
                            )
                            self._state.steps.append(step_record)
                            
                            # 不标记 verification_done，允许重新验证新答案
                            # 但增加计数器防止死循环
                            continue  # 继续下一个step，让agent重新推理
                    else:
                        # 验证未启用、已用完验证次数，或不需要验证，直接接受答案
                        if self._state.verification_attempt_count >= max_verification_attempts:
                            logger.warning(f"Max verification attempts ({max_verification_attempts}) reached. Accepting answer without verification.")
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
