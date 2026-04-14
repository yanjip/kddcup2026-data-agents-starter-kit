from __future__ import annotations

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
from data_agent_baseline.agents.verification_agent import (
    VerificationAgent,
    VerificationAgentConfig,
    VerificationResult,
    should_verify_answer,
)
from data_agent_baseline.benchmark.schema import AnswerTable, PublicTask
from data_agent_baseline.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class OrchestratorConfig:
    max_main_steps: int = 8
    max_subagents: int = 3


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
    verification_done: bool = False  # 标记是否已完成验证
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
        enable_verification: bool = True,  # 是否启用验证
    ) -> None:
        self.model = model
        self.tools = tools
        self.config = config
        self._enable_verification = enable_verification
        self._subagents: list[SubAgent] = []
        self._state = OrchestratorRuntimeState()
        self._verifier: VerificationAgent | None = None

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
            config=SubAgentConfig(max_steps=self.config.max_subagent_steps, name=subagent_name),
            inherited_messages=inherited_messages,
        )
        self._subagents.append(subagent)

        class SubTask:
            def __init__(self, desc: str, ctx: str, out: str, task_id: str, context_dir: Path):
                self.task_id = task_id
                self.question = f"{desc}\n\nContext: {ctx}\n\nExpected output: {out}"
                self.context_dir = context_dir

        sub_task = SubTask(task_description, task_context, expected_output, f"{task.task_id}_{subagent_name}", task.context_dir)
        return subagent.run(sub_task)

    def _verify_answer(
        self, 
        task: PublicTask, 
        proposed_answer: AnswerTable,
        original_thought: str = ""
    ) -> bool:
        """
        验证答案
        
        Args:
            task: 原始任务
            proposed_answer: 候选答案
            original_thought: 原始推理过程
            
        Returns:
            bool: 验证是否通过
        """
        # 初始化验证Agent（如果未初始化）
        if self._verifier is None:
            self._verifier = VerificationAgent(
                name="verifier",
                model=self.model,
                tools=self.tools,
                config=VerificationAgentConfig(max_steps=6),
            )
        
        # 执行验证
        verification_result = self._verifier.run(
            task=task,
            proposed_answer=proposed_answer,
            original_reasoning=original_thought
        )
        
        # 保存验证结果
        self._state.verification_result = verification_result
        
        # 根据置信度和验证结果判断是否通过
        # 置信度 > 0.7 且验证结果为有效才算通过
        passed = verification_result.is_valid and verification_result.confidence >= 0.7
        
        return passed

    def run(self, task: PublicTask) -> OrchestratorRunResult:
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
            raw_response = self.model.complete(messages)

            try:
                model_step = parse_model_step(raw_response)

                # Handle fork_subagent action internally (not through tool registry)
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
                        ok=fork_result.success,
                    )
                    self._state.steps.append(step_record)
                    continue

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
                    # 答案提交，进行验证（如果启用且未验证过）
                    if (self._enable_verification 
                        and not self._state.verification_done 
                        and tool_result.answer
                        and should_verify_answer(task, tool_result.answer)):
                        
                        verification_passed = self._verify_answer(task, tool_result.answer, model_step.thought)
                        
                        if verification_passed:
                            # 验证通过，接受答案
                            self._state.answer = tool_result.answer
                            break
                        else:
                            # 验证失败，拒绝答案并继续推理
                            # 添加验证失败的观察，让agent重新推理
                            verification_failed_observation = {
                                "ok": False,
                                "tool": "verification",
                                "content": {
                                    "status": "rejected",
                                    "reason": self._state.verification_result.reasoning if self._state.verification_result else "Answer verification failed",
                                    "suggested_fix": self._state.verification_result.suggested_fix if self._state.verification_result else None,
                                },
                            }
                            
                            step_record = OrchestratorStepRecord(
                                step_index=step_index,
                                thought=f"[VERIFICATION FAILED] {model_step.thought}",
                                action="verification_failed",
                                action_input={},
                                raw_response="",
                                observation=verification_failed_observation,
                                ok=False,
                            )
                            self._state.steps.append(step_record)
                            
                            # 标记已验证过，避免死循环
                            self._state.verification_done = True
                            continue  # 继续下一个step，让agent重新推理
                    else:
                        # 验证未启用或已验证过，直接接受答案
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
