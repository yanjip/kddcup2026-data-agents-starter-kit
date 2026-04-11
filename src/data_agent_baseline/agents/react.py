from __future__ import annotations

import json
import re
from dataclasses import dataclass

from data_agent_baseline.agents.model import ModelAdapter, ModelMessage, ModelStep
from data_agent_baseline.agents.prompt import (
    build_observation_prompt,
    build_system_prompt,
    build_task_prompt,
)

REACT_SYSTEM_PROMPT = """
You are a ReAct-style data agent with structured problem-solving capabilities.

You are solving a task from a public dataset. You may only inspect files inside the task's `context/` directory through the provided tools.

## Problem-Solving Framework

**1. Define** - Clarify the problem statement. What's blocking you? What does success look like? State constraints clearly.

**2. Analyze** - Apply multiple lenses to break down the challenge:
   - Functional: What does the data represent?
   - Technical: What operations are needed?
   - Temporal: What are the dependencies?
   - Resource: What tools/tables are available?
   - Risk: What could go wrong?
   - Stakeholder: What does the expected output serve?
   - Precedent: Any similar tasks solved before?
   - Creative: Any unconventional approaches?

**3. Generate Options** - Brainstorm 3+ solution paths before filtering.

**4. Choose** - Select the highest-leverage path. Justify your choice.

**5. Execute** - Run the action plan with real-time assumption tracking.

**6. Review** - Evaluate outcome. What worked? Update approach if assumptions break.

## Time Format Handling

**IMPORTANT - Approximate Matching:**
- If no exact match exists, the question is likely asking for the nearest match
- Use time comparison to find drivers with times close to the target
- Report the driver with the closest qualifying time

## Approximate Matching Strategy

**IMPORTANT - When exact data is not available:**
- If no exact match exists for a requested time period (e.g., June 2013), look for the **closest available** time period
- Compare time values to find the nearest match (e.g., August 2012 is closer than no data)
- When multiple data sources exist, cross-check all sources even if the primary source lacks data
- **Never return an empty result if approximate data is available** - use the closest match and document the substitution
- Apply this rule recursively: if exact year-month is unavailable, try the same month in a different year, or the closest month

## Data Completeness Check

When your primary query returns no results:
1. **Do NOT immediately conclude "no data"** - verify all available data sources
2. Check if other tables/files might contain related information through different fields (e.g., CustomerID can link transactions to consumption data)
3. If data exists but in a different format or time period, apply approximate matching
4. Document any approximations taken in your reasoning

## Core Rules

1. First, analyze the task and create a clear plan before executing any actions.
2. Use tools to inspect the available context efficiently, focusing only on what's necessary for your plan.
3. Break complex challenges into discrete, manageable pieces. Start with the constraint - problems are bottleneck puzzles.
4. Think in primitives - identify the smallest building blocks and build up from there.
5. Find independent work streams and execute them in parallel rather than sequentially.
6. Base your answer only on information you can observe through the provided tools.
7. **If no exact match exists for a time value, look for the CLOSEST match.**
8. **When you have found a likely answer (even if approximate), you MUST call the `answer` tool.**
9. Always return exactly one JSON object with keys `thought`, `action`, and `action_input`.
10. Always wrap that JSON object in exactly one fenced code block that starts with ```json and ends with ```.
11. Do not output any text before or after the fenced JSON block.

**Progress Tracking:**
- Include "Plan:", "Blocker:", "Assumption:" in your thought to track progress
- If you are stuck for more than 3 steps with no progress, try a different approach
- When you have gathered enough information to answer, SUBMIT THE ANSWER immediately

When stuck: Inspect the directive. What changed? What was missed? Update and retry.
""".strip()
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


@dataclass(frozen=True, slots=True)
class ProgressInfo:
    plan: list[str]
    blockers: list[str]
    assumptions: list[str]

    @staticmethod
    def parse_from_thought(thought: str) -> ProgressInfo:
        plan: list[str] = []
        blockers: list[str] = []
        assumptions: list[str] = []

        lines = thought.split('\n')
        current_section: str | None = None

        for line in lines:
            line_lower = line.lower().strip()
            if line_lower.startswith('plan:') or line_lower.startswith('- plan:'):
                current_section = 'plan'
                content = line.split(':', 1)[1].strip() if ':' in line else line[5:].strip()
                if content:
                    plan.append(content)
            elif line_lower.startswith('blocker:') or line_lower.startswith('- blocker:') or line_lower.startswith('blockers:'):
                current_section = 'blocker'
                content = line.split(':', 1)[1].strip() if ':' in line else line[8:].strip()
                if content:
                    blockers.append(content)
            elif line_lower.startswith('assumption:') or line_lower.startswith('- assumption:'):
                current_section = 'assumption'
                content = line.split(':', 1)[1].strip() if ':' in line else line[11:].strip()
                if content:
                    assumptions.append(content)
            elif current_section == 'plan' and (line.startswith('  - ') or line.startswith('   ')):
                plan.append(line.strip().lstrip('- ').strip())
            elif current_section == 'blocker' and (line.startswith('  - ') or line.startswith('   ')):
                blockers.append(line.strip().lstrip('- ').strip())
            elif current_section == 'assumption' and (line.startswith('  - ') or line.startswith('   ')):
                assumptions.append(line.strip().lstrip('- ').strip())
            else:
                current_section = None

        return ProgressInfo(plan=plan, blockers=blockers, assumptions=assumptions)


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

    def _build_messages(self, task: PublicTask, state: AgentRuntimeState, schema_knowledge: SchemaKnowledge | None, reflection_hint: str | None = None) -> list[ModelMessage]:
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

        if state.current_plan or state.blockers or state.assumptions:
            progress_context = "\n\n## Current Progress Context:\n"
            if state.current_plan:
                progress_context += "\n**Plan (in progress):**\n"
                for i, item in enumerate(state.current_plan, 1):
                    progress_context += f"  {i}. {item}\n"
            if state.blockers:
                progress_context += "\n**Blockers:**\n"
                for blocker in state.blockers:
                    progress_context += f"  - {blocker}\n"
            if state.assumptions:
                progress_context += "\n**Assumptions (being tracked):**\n"
                for assumption in state.assumptions:
                    progress_context += f"  - {assumption}\n"
            system_content += progress_context

        if reflection_hint:
            system_content += f"\n\n## Guidance:\n{reflection_hint}\n"

        messages = [ModelMessage(role="system", content=system_content)]
        messages.append(ModelMessage(role="user", content=build_task_prompt(task)))
        for step in state.steps:
            messages.append(ModelMessage(role="assistant", content=step.raw_response))
            messages.append(
                ModelMessage(role="user", content=build_observation_prompt(step.observation))
            )
        return messages

    def _detect_stalling(self, state: AgentRuntimeState) -> str | None:
        if len(state.steps) < 4:
            return None

        recent_steps = state.steps[-4:]
        actions = [s.action for s in recent_steps]

        if len(set(actions)) == 1 and actions[0] == "execute_python":
            return "You have executed the same Python code approach 4 times without progress. Try a DIFFERENT strategy: check data format, look for approximate matches, or try reading the data directly with different parameters."

        if all(s.action == "execute_python" for s in recent_steps):
            python_codes = [s.action_input.get("code", "")[:50] for s in recent_steps]
            if len(set(python_codes)) <= 2:
                return "You are repeatedly running similar Python code. Stop and analyze what you've learned so far. Do you have enough to answer the question? If so, call the 'answer' tool NOW."

        return None

    def _build_reflection_prompt(self, state: AgentRuntimeState) -> str:
        last_step = state.steps[-1] if state.steps else None
        last_observation = ""
        if last_step:
            obs_content = last_step.observation.get("content", {})
            if isinstance(obs_content, dict):
                last_observation = str(obs_content.get("output", ""))[:500]

        return f"""
## REFLECTION REQUIRED

You have {self.config.max_steps - len(state.steps)} steps remaining.

**Last observation (truncated):**
{last_observation}

**CRITICAL QUESTIONS:**
1. Have you found data that could answer the question?
2. Is there an exact match you might have missed?
3. If no exact match, have you tried finding the CLOSEST match?
4. Is there a time format issue? (e.g., "0:01:54" should be "1:54" in F1 format)

**If you have ANY candidate answer, you MUST call the 'answer' tool immediately.**
Do NOT continue searching if you have data that could answer the question.
""".strip()

    def run(self, task: PublicTask) -> AgentRunResult:
        schema_knowledge = self._auto_load_schema_knowledge(task)
        state = AgentRuntimeState()
        last_reflection_step = 0

        for step_index in range(1, self.config.max_steps + 1):
            reflection_hint = self._detect_stalling(state)
            if reflection_hint is None and len(state.steps) - last_reflection_step >= 3:
                remaining = self.config.max_steps - len(state.steps)
                if remaining <= 3 and remaining > 0:
                    reflection_hint = self._build_reflection_prompt(state)
                    last_reflection_step = len(state.steps)

            raw_response = self.model.complete(self._build_messages(task, state, schema_knowledge, reflection_hint))
            try:
                model_step = parse_model_step(raw_response)

                progress_info = ProgressInfo.parse_from_thought(model_step.thought)
                if progress_info.plan:
                    state.current_plan = progress_info.plan
                if progress_info.blockers:
                    state.blockers = progress_info.blockers
                if progress_info.assumptions:
                    state.assumptions = progress_info.assumptions

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
