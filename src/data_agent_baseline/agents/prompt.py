from __future__ import annotations

import json

from data_agent_baseline.benchmark.schema import PublicTask


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

## Core Rules

1. First, analyze the task and create a clear plan before executing any actions.
2. Use tools to inspect the available context efficiently, focusing only on what's necessary for your plan.
3. Break complex challenges into discrete, manageable pieces. Start with the constraint - problems are bottleneck puzzles.
4. Think in primitives - identify the smallest building blocks and build up from there.
5. Find independent work streams and execute them in parallel rather than sequentially.
6. Base your answer only on information you can observe through the provided tools.
7. The task is complete only when you call the `answer` tool.
8. The `answer` tool must receive a table with `columns` and `rows`.
9. Always return exactly one JSON object with keys `thought`, `action`, and `action_input`.
10. Always wrap that JSON object in exactly one fenced code block that starts with ```json and ends with ```.
11. Do not output any text before or after the fenced JSON block.

When stuck: Inspect the directive. What changed? What was missed? Update and retry.
""".strip()

RESPONSE_EXAMPLES = """
Example response when you need to inspect the context:
```json
{"thought":"Plan: 1. Inspect available files\\n2. Read knowledge.md\\n3. Execute query\\nBlocker: None yet\\nAssumption: context/ contains the data needed\\n\\nI should inspect the available files first.","action":"list_context","action_input":{"max_depth":4}}
```

Example response when you have the final answer:
```json
{"thought":"Plan: 1. ✓ Inspected context\\n2. ✓ Read knowledge.md\\n3. ✓ Computed result\\nBlocker: None\\nAssumption: Verified\\n\\nI have the final result table.","action":"answer","action_input":{"columns":["average_long_shots"],"rows":[["63.5"]]}}
```
""".strip()


def build_system_prompt(tool_descriptions: str, system_prompt: str | None = None) -> str:
    base_prompt = system_prompt or REACT_SYSTEM_PROMPT
    return (
        f"{base_prompt}\n\n"
        "Available tools:\n"
        f"{tool_descriptions}\n\n"
        f"{RESPONSE_EXAMPLES}\n\n"
        "You must always return a single ```json fenced block containing one JSON object "
        "with keys `thought`, `action`, and `action_input`, and no extra text."
    )


def build_task_prompt(task: PublicTask) -> str:
    return (
        f"Question: {task.question}\n"
        "All tool file paths are relative to the task context directory. "
        "When you have the final table, call the `answer` tool."
    )


def build_observation_prompt(observation: dict[str, object]) -> str:
    rendered = json.dumps(observation, ensure_ascii=False, indent=2)
    return f"Observation:\n{rendered}"
