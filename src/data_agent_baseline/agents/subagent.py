from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from data_agent_baseline.agents.model import ModelAdapter, ModelMessage
from data_agent_baseline.agents.parser import parse_model_step
from data_agent_baseline.agents.prompt import (
    build_observation_prompt,
    build_subagent_system_prompt,
    build_task_prompt,
)
from data_agent_baseline.agents.runtime import AgentRuntimeState, StepRecord
from data_agent_baseline.benchmark.schema import AnswerTable, PublicTask
from data_agent_baseline.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SchemaKnowledge:
    """存储从 knowledge.md 解析出的表结构和语义映射。

    Attributes:
        tables: 表名到列名列表的映射，例如 {"users": ["id", "name", "email"]}
        semantic_mappings: 语义映射，例如 {"driver": "车辆驾驶员"}
    """
    tables: dict[str, list[str]]
    semantic_mappings: dict[str, str]


@dataclass(frozen=True, slots=True)
class SubAgentConfig:
    max_steps: int = 16
    name: str = "subagent"


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
    _schema_knowledge: SchemaKnowledge | None = field(default=None, repr=False)

    def _auto_load_schema_knowledge(self, task: PublicTask) -> SchemaKnowledge | None:
        """自动从 knowledge.md 加载 Schema 知识。

        通过 list_context 查找 knowledge.md 文件，读取并解析其中的
        表结构和语义映射信息。

        Args:
            task: 当前任务对象

        Returns:
            SchemaKnowledge 对象，如果加载失败则返回 None
        """
        # 如果已经加载过，直接返回缓存
        if self._schema_knowledge is not None:
            return self._schema_knowledge

        try:
            list_result = self.tools.execute(task, "list_context", {"max_depth": 4})
            if not list_result.ok:
                logger.debug("list_context failed: %s", list_result.content.get("error"))
                return None

            entries = list_result.content.get("entries", [])
            knowledge_path = None
            for entry in entries:
                if entry.get("kind") == "file" and entry.get("path", "").endswith("knowledge.md"):
                    knowledge_path = entry["path"]
                    break

            if not knowledge_path:
                logger.debug("No knowledge.md found in context")
                return None

            read_result = self.tools.execute(task, "read_doc", {"path": knowledge_path})
            if not read_result.ok:
                logger.warning("Failed to read knowledge.md: %s", read_result.content.get("error"))
                return None

            content = read_result.content.get("preview", "")
            if not content:
                logger.warning("knowledge.md is empty")
                return None

            tables = self._parse_tables_from_knowledge(content)
            semantic_mappings = self._parse_semantic_mappings_from_knowledge(content)

            self._schema_knowledge = SchemaKnowledge(
                tables=tables, semantic_mappings=semantic_mappings
            )
            logger.info(
                "Loaded schema knowledge: %d tables, %d mappings",
                len(tables),
                len(semantic_mappings),
            )
            return self._schema_knowledge

        except Exception as exc:
            logger.exception("Unexpected error loading schema knowledge: %s", exc)
            return None

    def _parse_tables_from_knowledge(self, content: str) -> dict[str, list[str]]:
        """从 knowledge.md 内容中解析表结构。

        解析格式如：
        ### table_name
        - **column1**: description
        - **column2**: description

        Args:
            content: knowledge.md 的文件内容

        Returns:
            表名到列名列表的映射
        """
        tables: dict[str, list[str]] = {}
        lines = content.split("\n")
        current_table: str | None = None

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # 检测表名：### table_name
            if line.startswith("### "):
                current_table = line[4:].strip()
                if current_table:
                    tables[current_table] = []
            # 检测列名：- **column_name**: description
            elif current_table and line.startswith("- **"):
                try:
                    # 提取 **column_name** 部分
                    parts = line.split("**")
                    if len(parts) >= 2:
                        column_def = parts[1]
                        # 处理 column_name: description 格式
                        if ":" in column_def:
                            column_name = column_def.split(":")[0].strip()
                        else:
                            column_name = column_def.strip()
                        if column_name:
                            tables[current_table].append(column_name)
                except (IndexError, ValueError) as exc:
                    logger.debug("Failed to parse column from line '%s': %s", line, exc)
                    continue

        return tables

    def _parse_semantic_mappings_from_knowledge(self, content: str) -> dict[str, str]:
        """从 knowledge.md 内容中解析语义映射。

        解析格式如：
        **key**: value

        Args:
            content: knowledge.md 的文件内容

        Returns:
            语义映射字典
        """
        mappings: dict[str, str] = {}
        lines = content.split("\n")

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # 检测语义映射：**key**: value
            if ":**" in line and "**" in line:
                try:
                    parts = line.split(":**")
                    if len(parts) == 2:
                        key = parts[0].strip().replace("**", "")
                        value = parts[1].strip()
                        # 移除可能的尾部 **
                        if value.endswith("**"):
                            value = value[:-2].strip()
                        if key and value:
                            mappings[key] = value
                except (IndexError, ValueError) as exc:
                    logger.debug("Failed to parse mapping from line '%s': %s", line, exc)
                    continue

        return mappings

    def _build_messages_with_schema(
        self, task: PublicTask, schema_knowledge: SchemaKnowledge | None
    ) -> list[ModelMessage]:
        """构建消息列表，包含 Schema 知识。

        Args:
            task: 当前任务对象
            schema_knowledge: Schema 知识对象，可能为 None

        Returns:
            构建好的消息列表
        """
        # 构建系统提示词（仅在第一步添加）
        if not self.state.steps:
            system_prompt = build_subagent_system_prompt()
            messages = [ModelMessage(role="system", content=system_prompt)]
        else:
            messages = []
        
        messages.extend(self.inherited_messages.copy())

        # 构建任务提示词
        task_prompt = build_task_prompt(task)

        # 如果有 Schema 知识，添加到提示词中
        if schema_knowledge and (schema_knowledge.tables or schema_knowledge.semantic_mappings):
            schema_info = "\n\n## Schema Knowledge:\n"

            if schema_knowledge.tables:
                schema_info += "\nTables and columns:\n"
                for table, columns in schema_knowledge.tables.items():
                    schema_info += f"- {table}: {', '.join(columns)}\n"

            if schema_knowledge.semantic_mappings:
                schema_info += "\nSemantic mappings:\n"
                for key, value in schema_knowledge.semantic_mappings.items():
                    schema_info += f"- {key}: {value}\n"

            task_prompt += schema_info

        messages.append(ModelMessage(role="user", content=task_prompt))

        # 添加历史步骤
        for step in self.state.steps:
            messages.append(ModelMessage(role="assistant", content=step.raw_response))
            messages.append(ModelMessage(role="user", content=build_observation_prompt(step.observation)))

        return messages

    def run(self, task: PublicTask) -> ForkResult:
        self.sub_task = task
        self.state = AgentRuntimeState()

        # 自动加载 Schema 知识（第一次运行时）
        schema_knowledge = self._auto_load_schema_knowledge(task)

        for step_index in range(1, self.config.max_steps + 1):
            # Build messages with schema knowledge
            messages = self._build_messages_with_schema(task, schema_knowledge)

            raw_response = self.model.complete(messages)

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
                if not tool_result.ok:
                    self.state.failure_reason = tool_result.content.get("error", "Tool execution failed")

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

        self.state.failure_reason = f"SubAgent did not complete within max_steps ({self.config.max_steps})."
        return ForkResult(
            subagent_name=self.name,
            success=False,
            result=None,
            steps=list(self.state.steps),
            error=self.state.failure_reason,
        )
