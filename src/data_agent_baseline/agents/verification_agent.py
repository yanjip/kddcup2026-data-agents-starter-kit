"""
验证Agent模块 - 用于反向验证答案的正确性

设计理念：有些问题顺着推很难，但倒着验证很简单。
验证Agent接收问题和候选答案，通过独立验证来确认答案的可靠性。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from data_agent_baseline.agents.model import ModelAdapter, ModelMessage

logger = logging.getLogger(__name__)
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

    def _identify_data_sources(self, task: PublicTask) -> str | None:
        """
        自动识别任务上下文中的可用数据源
        
        通过 list_context 工具扫描文件，识别 CSV、SQLite 等数据源
        
        Args:
            task: 当前任务
            
        Returns:
            数据源描述字符串，如果识别失败返回 None
        """
        try:
            list_result = self.tools.execute(task, "list_context", {"max_depth": 4})
            if not list_result.ok:
                return None
            
            entries = list_result.content.get("entries", [])
            csv_files = []
            sqlite_files = []
            doc_files = []
            
            for entry in entries:
                path = entry.get("path", "")
                kind = entry.get("kind", "")
                
                if kind == "file":
                    if path.endswith(".csv"):
                        csv_files.append(path)
                    elif path.endswith(".db") or path.endswith(".sqlite"):
                        sqlite_files.append(path)
                    elif path.endswith(".md") or path.endswith(".txt"):
                        doc_files.append(path)
            
            if not (csv_files or sqlite_files or doc_files):
                return None
            
            info_parts = []
            if csv_files:
                info_parts.append(f"CSV files: {', '.join(csv_files)}")
            if sqlite_files:
                info_parts.append(f"SQLite databases: {', '.join(sqlite_files)}")
            if doc_files:
                info_parts.append(f"Documentation: {', '.join(doc_files)}")
            
            return "\n".join(info_parts)
            
        except Exception:
            return None

    def _build_system_prompt(self) -> str:
        """构建验证Agent的系统提示词"""
        tool_descriptions = self.tools.describe_for_prompt()
        return f"""You are a Verification Agent specialized in reverse-validating answers.

## Your Mission
Verify if a given answer is correct by working backwards from the answer to the data sources.
Some questions are hard to solve forward but easy to verify backward.

## Verification Strategy
1. **Understand the Question**: What is being asked? What would constitute a correct answer?
2. **Analyze the Proposed Answer**: What does the answer claim? What values/calculations does it contain?
3. **Reproduce the Calculation**: Try to reproduce the same result using the data sources
4. **Find Evidence**: Use tools to locate data that confirms or contradicts the answer
5. **Cross-Reference**: Check if the answer aligns with the actual data
6. **Judge**: Determine if the answer is VALID or INVALID with confidence score

## Key Principles
- **Be Skeptical**: Don't assume the answer is correct. Actively look for contradictions.
- **Find Data Source**: Always identify which specific data supports or refutes the answer
- **Reproduce Results**: Try to get the same numbers as the proposed answer
- **Quantify When Possible**: If the answer claims "X = 5", verify by recalculating from raw data
- **Check Completeness**: Ensure the answer addresses all parts of the question
- **Use Same Sources**: Follow the main agent's data source choices (CSV vs SQLite)

## Confidence Scoring Guide
- **0.9-1.0**: Verified by direct calculation from data, exact match
- **0.7-0.89**: Strong evidence supports answer, minor rounding differences acceptable
- **0.5-0.69**: Partial verification, some assumptions made, or small discrepancies
- **0.3-0.49**: Weak evidence, significant concerns or unable to fully verify
- **0.0-0.29**: Clear contradiction with data, answer is wrong

## Available Tools
{tool_descriptions}

## Response Format
You must always return a single ```json fenced block containing one JSON object with keys `thought`, `action`, and `action_input`.

**CRITICAL**: `action_input` must always be a JSON object (dictionary), never a string or array.

### Correct format:
```json
{"thought":"I will list the files first","action":"list_context","action_input":{"max_depth":4}}
```

### Incorrect format (DO NOT USE):
```json
{"thought":"I will list files","action":"list_context","action_input":"max_depth: 4"}
```

When you have completed verification, use the `answer` tool with this format:
```json
{"thought":"Verification complete. The answer is VALID because...","action":"answer","action_input":{"columns":["result","confidence"],"rows":[["VALID",0.95]]}}
```

## Core Rules
1. **ALWAYS start by listing available files** using `list_context` to see what data sources exist
2. **Always verify against actual data sources**, not just logic or assumptions
3. **Try to reproduce the exact calculation** - if answer is 99.5%, calculate it yourself
4. **If verification fails, explain what the correct answer should be** with your calculation
5. **Be specific about data sources** - cite which files/tables you used
6. **Confidence should reflect how strongly the data supports the answer**
7. **If you cannot verify due to data access issues, report low confidence (<0.5)**
8. **NEVER assume file paths** - always check what files are actually available first
""".strip()

    def _build_verification_prompt(
        self, 
        task: PublicTask, 
        proposed_answer: AnswerTable,
        original_reasoning: str = "",
        execution_history: list[dict[str, Any]] | None = None
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
"""
        
        # 添加主 Agent 的执行历史，帮助验证 Agent 了解数据源和推理路径
        if execution_history:
            prompt += "\n## Main Agent Execution History\n"
            prompt += "The main agent used the following approach to arrive at the answer:\n"
            for i, step in enumerate(execution_history[-10:], 1):  # 只显示最近10步
                action = step.get('action', 'unknown')
                thought = step.get('thought', '')
                if action and action not in ['__error__', 'verification_failed']:
                    prompt += f"\nStep {i}:\n"
                    prompt += f"- Action: {action}\n"
                    if thought:
                        # 截断过长的 thought
                        thought_summary = thought[:200] + "..." if len(thought) > 200 else thought
                        prompt += f"- Thought: {thought_summary}\n"
            prompt += "\nUse this information to understand:\n"
            prompt += "1. Which data sources the main agent used (CSV files, SQLite tables, etc.)\n"
            prompt += "2. What calculation or filtering steps were performed\n"
            prompt += "3. Focus your verification on the same data sources and approach\n"
        
        prompt += """\n## Your Task
Verify if the proposed answer is CORRECT by:
1. Understanding what the question is asking
2. Checking if the answer can be derived from the available data
3. Looking for evidence that supports or contradicts the answer
4. Determining if the answer is complete and accurate

**IMPORTANT**: Use the same data sources as the main agent. If they used CSV files, you should use CSV files. If they used SQLite, you should use SQLite.

Start by exploring the data sources to find evidence.
"""
        return prompt

    async def run(
        self, 
        task: PublicTask, 
        proposed_answer: AnswerTable,
        original_reasoning: str = "",
        execution_history: list[dict[str, Any]] | None = None
    ) -> VerificationResult:
        """
        执行验证
        
        Args:
            task: 原始任务
            proposed_answer: 需要验证的答案
            original_reasoning: 原始推理过程（可选）
            execution_history: 主 Agent 的执行历史（可选）
            
        Returns:
            VerificationResult: 验证结果
        """
        self.state = AgentRuntimeState()
        
        system_prompt = self._build_system_prompt()
        messages: list[ModelMessage] = [
            ModelMessage(role="system", content=system_prompt)
        ]
        
        verification_prompt = self._build_verification_prompt(
            task, proposed_answer, original_reasoning, execution_history
        )
        messages.append(ModelMessage(role="user", content=verification_prompt))
        
        # 自动识别可用数据源并添加到提示词
        data_source_info = self._identify_data_sources(task)
        if data_source_info:
            messages.append(ModelMessage(
                role="user", 
                content=f"## Available Data Sources\n{data_source_info}\n\nUse these data sources for verification."
            ))

        for step_index in range(1, self.config.max_steps + 1):
            raw_response = await self.model.complete(messages)
            model_step = None

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
                # 验证执行过程中出错，记录错误并告知验证Agent让它修复
                error_msg = str(exc).lower()
                
                logger.warning(f"Verification agent step {step_index} encountered error: {error_msg}")
                
                # 判断是解析错误还是工具执行错误
                if model_step is None:
                    # JSON解析错误，给验证Agent具体的格式指导
                    error_feedback = (
                        f"Your response could not be parsed. Error: {str(exc)}\n\n"
                        f"Please ensure your response follows this exact format:\n"
                        f'```json\n'
                        f'{{"thought": "your reasoning here", "action": "tool_name", "action_input": {{"key": "value"}}}}\n'
                        f'```\n\n'
                        f'Important: action_input must be a JSON object {{}}, not a string or array.'
                    )
                    action_name = "parse_error"
                    action_input_val = {}
                else:
                    # 工具执行错误
                    error_feedback = f"Tool execution failed: {str(exc)}"
                    action_name = model_step.action
                    action_input_val = model_step.action_input
                
                # 将错误信息反馈给验证Agent
                observation = {
                    "ok": False,
                    "tool": action_name,
                    "content": {"error": error_feedback},
                }
                
                step_record = StepRecord(
                    step_index=step_index,
                    thought=f"Error occurred: {str(exc)}",
                    action=action_name,
                    action_input=action_input_val,
                    raw_response=raw_response,
                    observation=observation,
                    ok=False,
                )
                self.state.steps.append(step_record)
                
                # 继续对话，让验证Agent尝试修复错误
                messages.append(ModelMessage(role="assistant", content=raw_response))
                messages.append(
                    ModelMessage(role="user", content=build_observation_prompt(observation))
                )
                
                # 继续下一个step，不立即返回None
                continue

        # 验证步骤耗尽，返回 None 表示验证无法完成
        # 这与验证判断答案错误区分开，让 orchestrator 接受答案
        logger.warning("Verification did not complete within max_steps. Returning None to accept answer.")
        return None

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
        
        # 尝试从 final_thought 中提取额外信息
        thought_lower = final_thought.lower()
        
        # 如果从 thought 中检测到明确的验证失败信号，降低置信度
        if any(phrase in thought_lower for phrase in ['incorrect', 'wrong', 'does not match', 'contradiction', 'failed']):
            if confidence > 0.5:
                confidence = 0.3  # 降低置信度
        
        # 如果从 thought 中检测到明确的验证通过信号，提高置信度
        if any(phrase in thought_lower for phrase in ['correct', 'matches', 'verified', 'confirmed']):
            if confidence < 0.7:
                confidence = 0.8  # 提高置信度

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
                        if isinstance(value, str) and value and value.lower() not in ('none', 'none needed', 'n/a', ''):
                            suggested_fix = value
                    
                    elif "source" in col_lower or "data" in col_lower:
                        # 数据源列
                        if isinstance(value, str) and value:
                            verified_data_source = value
        
        # 后处理：确保置信度与验证结果一致
        # 如果标记为 VALID 但置信度太低，提高置信度
        if is_valid and confidence < 0.5:
            confidence = 0.7
        # 如果标记为 INVALID 但置信度太高，降低置信度
        elif not is_valid and confidence > 0.8:
            confidence = 0.6

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
    # 对所有非空答案进行验证（不区分难度）
    # 验证可以帮助发现错误，提高整体准确率
    if answer and answer.rows and len(answer.rows) > 0:
        return True
    
    return False
