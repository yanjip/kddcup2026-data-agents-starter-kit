"""
验证Agent模块 - 用于反向验证答案的正确性

设计理念：有些问题顺着推很难，但倒着验证很简单。
验证Agent接收问题和候选答案，通过独立验证来确认答案的可靠性。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from data_agent_baseline.agents.model import ModelAdapter, ModelMessage
from data_agent_baseline.agents.parser import parse_model_step
from data_agent_baseline.agents.prompt import (
    build_observation_prompt,
    build_task_prompt,
)
from data_agent_baseline.agents.runtime import AgentRuntimeState, StepRecord
from data_agent_baseline.benchmark.schema import AnswerTable, PublicTask
from data_agent_baseline.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """验证结果"""
    is_valid: bool  # 验证是否通过
    confidence: float  # 置信度 0-1
    reasoning: str  # 验证推理过程
    suggested_fix: str | None = None  # 如果验证失败，建议的修复方向
    verified_data_source: str | None = None  # 验证所使用的数据源


@dataclass(frozen=True, slots=True)
class VerificationAgentConfig:
    """验证Agent配置"""
    max_steps: int = 6  # 验证步骤数限制，验证应该快速完成
    name: str = "verifier"


@dataclass(slots=True)
class VerificationAgent:
    """
    验证Agent - 专门用于反向验证答案
    
    工作流程：
    1. 接收原始问题和候选答案
    2. 独立探索数据源进行验证
    3. 判断答案是否正确/合理
    4. 返回验证结果和建议
    """
    name: str
    model: ModelAdapter
    tools: ToolRegistry
    config: VerificationAgentConfig
    state: AgentRuntimeState = field(default_factory=AgentRuntimeState)

    def _build_system_prompt(self) -> str:
        """构建验证Agent的系统提示词"""
        tool_descriptions = self.tools.describe_for_prompt()
        return f"""You are a Verification Agent specialized in reverse-validating answers.

## Your Mission
Verify if a given answer is correct by working backwards from the answer to the data sources.
Some questions are hard to solve forward but easy to verify backward.

## Verification Strategy
1. **Understand the Question**: What is being asked? What would constitute a correct answer?
2. **Analyze the Proposed Answer**: What does the answer claim?
3. **Find Evidence**: Use tools to locate data that confirms or contradicts the answer
4. **Cross-Reference**: Check if the answer aligns with the actual data
5. **Judge**: Determine if the answer is VALID or INVALID

## Key Principles
- **Be Skeptical**: Don't assume the answer is correct. Actively look for contradictions.
- **Find Data Source**: Always identify which specific data supports or refutes the answer
- **Quantify When Possible**: If the answer claims "X = 5", verify by recalculating
- **Check Completeness**: Ensure the answer addresses all parts of the question

## Available Tools
{tool_descriptions}

## Response Format
You must always return a single ```json fenced block containing one JSON object with keys `thought`, `action`, and `action_input`.

When you have completed verification, use the `answer` tool with this format:
```json
{{"thought":"Verification complete. I found [evidence]. The answer is [VALID/INVALID] because...","action":"answer","action_input":{{"columns":["verification_result","confidence","reasoning"],"rows":[["VALID",0.95,"The answer matches the data in X.csv..."]]}}}}
```

## Core Rules
1. Always verify against actual data sources, not just logic
2. If verification fails, explain what the correct answer should be
3. Be concise - verification should be quick and focused
4. Confidence should reflect how strongly the data supports the answer
""".strip()

    def _build_verification_prompt(
        self, 
        task: PublicTask, 
        proposed_answer: AnswerTable,
        original_reasoning: str = ""
    ) -> str:
        """构建验证任务的提示词"""
        answer_dict = proposed_answer.to_dict()
        
        prompt = f"""## Original Question
{task.question}

## Proposed Answer to Verify
Columns: {answer_dict['columns']}
Rows: {answer_dict['rows']}

## Original Reasoning (if available)
{original_reasoning if original_reasoning else "No reasoning provided."}

## Your Task
Verify if the proposed answer is CORRECT by:
1. Understanding what the question is asking
2. Checking if the answer can be derived from the available data
3. Looking for evidence that supports or contradicts the answer
4. Determining if the answer is complete and accurate

Start by exploring the data sources to find evidence.
"""
        return prompt

    def run(
        self, 
        task: PublicTask, 
        proposed_answer: AnswerTable,
        original_reasoning: str = ""
    ) -> VerificationResult:
        """
        执行验证
        
        Args:
            task: 原始任务
            proposed_answer: 需要验证的答案
            original_reasoning: 原始推理过程（可选）
            
        Returns:
            VerificationResult: 验证结果
        """
        self.state = AgentRuntimeState()
        
        system_prompt = self._build_system_prompt()
        messages: list[ModelMessage] = [
            ModelMessage(role="system", content=system_prompt)
        ]
        
        verification_prompt = self._build_verification_prompt(
            task, proposed_answer, original_reasoning
        )
        messages.append(ModelMessage(role="user", content=verification_prompt))

        for step_index in range(1, self.config.max_steps + 1):
            raw_response = self.model.complete(messages)

            try:
                model_step = parse_model_step(raw_response)

                tool_result = self.tools.execute(
                    task, model_step.action, model_step.action_input
                )
                
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

                # 如果验证Agent给出了答案，解析验证结果
                if tool_result.is_terminal and tool_result.answer:
                    return self._parse_verification_result(
                        tool_result.answer, 
                        model_step.thought
                    )

                # 继续对话
                messages.append(ModelMessage(role="assistant", content=raw_response))
                messages.append(
                    ModelMessage(role="user", content=build_observation_prompt(observation))
                )

            except Exception as exc:
                # 验证出错，返回无效结果
                return VerificationResult(
                    is_valid=False,
                    confidence=0.0,
                    reasoning=f"Verification failed with error: {str(exc)}",
                    suggested_fix="Please re-examine the data sources."
                )

        # 验证步骤耗尽，返回无效结果
        return VerificationResult(
            is_valid=False,
            confidence=0.0,
            reasoning="Verification did not complete within max_steps.",
            suggested_fix="The verification process timed out. Please try a different approach."
        )

    def _parse_verification_result(
        self, 
        answer: AnswerTable,
        final_thought: str
    ) -> VerificationResult:
        """从验证Agent的答案中解析验证结果"""
        # 默认结果
        is_valid = False
        confidence = 0.5
        reasoning = final_thought
        suggested_fix = None
        verified_data_source = None

        # 解析答案表格
        if answer.rows and len(answer.rows) > 0:
            row = answer.rows[0]
            columns = answer.columns
            
            # 尝试找到验证结果列
            for i, col in enumerate(columns):
                col_lower = col.lower()
                if i < len(row):
                    value = row[i]
                    
                    if "result" in col_lower or "valid" in col_lower:
                        # 验证结果列
                        if isinstance(value, str):
                            is_valid = value.upper() in ("VALID", "TRUE", "YES", "CORRECT", "PASS")
                        elif isinstance(value, bool):
                            is_valid = value
                    
                    elif "confidence" in col_lower:
                        # 置信度列
                        try:
                            confidence = float(value)
                        except (ValueError, TypeError):
                            confidence = 0.5
                    
                    elif "reasoning" in col_lower or "explanation" in col_lower:
                        # 推理列
                        if isinstance(value, str):
                            reasoning = value
                    
                    elif "suggested" in col_lower or "fix" in col_lower:
                        # 建议修复列
                        if isinstance(value, str) and value:
                            suggested_fix = value
                    
                    elif "source" in col_lower or "data" in col_lower:
                        # 数据源列
                        if isinstance(value, str) and value:
                            verified_data_source = value

        return VerificationResult(
            is_valid=is_valid,
            confidence=confidence,
            reasoning=reasoning,
            suggested_fix=suggested_fix,
            verified_data_source=verified_data_source
        )


def should_verify_answer(task: PublicTask, answer: AnswerTable) -> bool:
    """
    判断是否应该对答案进行验证
    
    可以根据任务难度、答案复杂度等条件决定是否验证
    
    Args:
        task: 任务对象
        answer: 候选答案
        
    Returns:
        bool: 是否应该验证
    """
    # 可以根据需要添加更多判断逻辑
    # 例如：只对hard/extreme难度的任务进行验证
    if hasattr(task, 'difficulty') and task.difficulty:
        difficulty = task.difficulty.lower()
        if difficulty in ('hard', 'extreme', 'medium'):
            return True
    
    # 默认对非空答案进行验证
    if answer and answer.rows and len(answer.rows) > 0:
        return True
    
    return False
