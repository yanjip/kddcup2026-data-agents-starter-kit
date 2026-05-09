from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from data_agent_baseline.benchmark.schema import AnswerTable, PublicTask
from data_agent_baseline.tools.filesystem import (
    list_context_tree,
    read_csv_preview,
    read_doc_preview,
    read_json_preview,
    resolve_context_path,
)
from data_agent_baseline.tools.math_calc import calculate as math_calculate
from data_agent_baseline.tools.python_exec import execute_python_code
from data_agent_baseline.tools.sqlite import execute_read_only_sql, inspect_sqlite_schema

EXECUTE_PYTHON_TIMEOUT_SECONDS = 30


# =============================================================================
# SQL Query Logic Safeguards — injected into execute_context_sql tool description
# =============================================================================

SQL_SAFEGUARD_RULES = """\
CRITICAL — Before writing SQL, obey these rules or you WILL get wrong answers:

1. AND vs OR: When the question says "have X AND Y", it means BOTH conditions on the SAME entity.
   WRONG: WHERE element = 'p' OR element = 'n'.
   RIGHT: Use a self-join, subquery, or GROUP BY + HAVING COUNT(DISTINCT ...) >= 2.

2. Aggregation Sanity Check: After computing AVG/SUM/COUNT/ratio, ALWAYS print intermediate values and sanity-check them.
   Is the percentage between 0-100? Is the count plausible? Rule: print(f'Result: {value}, Sanity: check') before submitting.

3. Percentage and Ratio: Before calculating, explicitly write: numerator = ???, denominator = ???. Print BOTH separately before dividing.
   "How many times is A compared to B" → A / B (never B / A).

4. NULL Preservation in JOINs: When the question says "List the names AND funding types", some records may have NULL funding type — they MUST still appear.
   Use LEFT JOIN (not INNER JOIN) when the question asks to "list" or "show" entities that may lack some attributes.

5. Ranking and "Nth" Queries: When finding "the driver who ranked 2nd", ALWAYS: 1) Print the TOP 5 results first. 2) Verify the correct row is selected. 3) Check for ties.
   Never blindly take LIMIT 1 OFFSET N.

6. Threshold Lookups: Words like "normal", "abnormal", "severe", "high", "low" ALWAYS have specific numeric definitions in knowledge.md.
   You MUST look up the EXACT threshold from knowledge.md before writing any filter.

7. Lowest/Highest with Ties: When a question asks "which X has the lowest Y", there may be MULTIPLE Xs tied at the lowest value.
   Return ALL tied results, not just one."""

SQL_FAILURE_CASES = """\
REAL FAILURE CASES (learn from these mistakes):

Case A (task_194): Question: bonds with BOTH element 'p' AND element 'n'. WRONG: WHERE element='p' OR element='n' → returned 270 rows. RIGHT: self-join / HAVING COUNT(DISTINCT element)>=2 → 7 rows. Lesson: AND on same column needs self-join or HAVING, never OR.

Case B (task_173): Question: list schools AND their funding types. WRONG: INNER JOIN → only 1 row (5 schools had NULL funding). RIGHT: LEFT JOIN → 6 rows. Lesson: "list X and Y" where Y may be NULL → LEFT JOIN.

Case C (task_355): Question: average monthly spending per customer. WRONG: forgot to divide by customer count → returned 82,027,220. RIGHT: SUM / (12 * customer_count) → 6,836. Lesson: always sanity-check aggregation magnitude.

Case D (task_243): Question: ratio of A compared to B. WRONG: B / A = 0.103. RIGHT: A / B = 0.375. Lesson: "A compared to B" → A is numerator, B is denominator.

Case E (task_89): Question: driver who ranked 2nd. WRONG: LIMIT 1 OFFSET 1 → picked wrong row (ties + wrong sort direction). RIGHT: print TOP 5 first, then select. Lesson: always verify ranking with visual inspection.

Case F (task_25): Question: patients with "normal" white blood cell count. WRONG: assumed 4000-10000. RIGHT: knowledge.md says 3500-9500. Lesson: ALWAYS look up thresholds in knowledge.md.

Case G (task_408): Question: driver with fewest wins. WRONG: LIMIT 1 → missed 3 tied drivers. RIGHT: return all rows with MIN value. Lesson: check for ties before limiting.

Case H (task_200): Question: count of records meeting condition X. WRONG: COUNT(*) counted all rows including NULLs. RIGHT: COUNT(column) or COUNT(DISTINCT column). Lesson: COUNT(*) ≠ COUNT(column)."""


# =============================================================================
# Python Calculation Safeguards — injected into execute_python tool description
# =============================================================================

PYTHON_CALC_SAFEGUARDS = """\
When using Python for calculations:
- Aggregation Sanity Check: After computing AVG/SUM/COUNT/ratio, ALWAYS print intermediate values. Verify percentages are 0-100, counts are plausible.
- Ratio Direction: Before dividing, explicitly write numerator = ???, denominator = ???. "A compared to B" → A / B.
- Threshold Lookups: Words like "normal/abnormal/severe/high/low" have exact numeric definitions in knowledge.md. Read knowledge.md FIRST before coding any filter."""


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    ok: bool
    content: dict[str, Any]
    is_terminal: bool = False
    answer: AnswerTable | None = None


ToolHandler = Callable[[PublicTask, dict[str, Any]], ToolExecutionResult]


def _list_context(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    max_depth = int(action_input.get("max_depth", 4))
    return ToolExecutionResult(ok=True, content=list_context_tree(task, max_depth=max_depth))


def _read_csv(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = str(action_input["path"])
    max_rows = int(action_input.get("max_rows", 20))
    return ToolExecutionResult(ok=True, content=read_csv_preview(task, path, max_rows=max_rows))


def _read_json(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = str(action_input["path"])
    max_chars = int(action_input.get("max_chars", 4000))
    return ToolExecutionResult(ok=True, content=read_json_preview(task, path, max_chars=max_chars))


def _read_doc(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = str(action_input["path"])
    max_chars = int(action_input.get("max_chars", 4000))
    return ToolExecutionResult(ok=True, content=read_doc_preview(task, path, max_chars=max_chars))


def _inspect_sqlite_schema(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = resolve_context_path(task, str(action_input["path"]))
    return ToolExecutionResult(ok=True, content=inspect_sqlite_schema(path))


def _execute_context_sql(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = resolve_context_path(task, str(action_input["path"]))
    sql = str(action_input["sql"])
    limit = int(action_input.get("limit", 200))
    return ToolExecutionResult(ok=True, content=execute_read_only_sql(path, sql, limit=limit))


def _execute_python(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    if "code" not in action_input:
        return ToolExecutionResult(
            ok=False,
            content={
                "success": False,
                "error": "Missing required parameter 'code'. The action_input must contain a 'code' key with Python code string.",
                "output": "",
                "stderr": "",
            },
        )
    code = str(action_input["code"])
    content = execute_python_code(
        context_root=task.context_dir,
        code=code,
        timeout_seconds=EXECUTE_PYTHON_TIMEOUT_SECONDS,
    )
    return ToolExecutionResult(ok=bool(content.get("success")), content=content)


def _calculate_math(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    expression = str(action_input["expression"])
    return ToolExecutionResult(ok=True, content=math_calculate(expression))


def _answer(_: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    columns = action_input.get("columns")
    rows = action_input.get("rows")
    if not isinstance(columns, list) or not columns or not all(isinstance(item, str) for item in columns):
        raise ValueError("answer.columns must be a non-empty list of strings.")
    if not isinstance(rows, list):
        raise ValueError("answer.rows must be a list.")

    normalized_rows: list[list[Any]] = []
    for row in rows:
        if not isinstance(row, list):
            raise ValueError("Each answer row must be a list.")
        if len(row) != len(columns):
            raise ValueError("Each answer row must match the number of columns.")
        normalized_rows.append(list(row))

    answer = AnswerTable(columns=list(columns), rows=normalized_rows)
    return ToolExecutionResult(
        ok=True,
        content={
            "status": "submitted",
            "column_count": len(columns),
            "row_count": len(normalized_rows),
        },
        is_terminal=True,
        answer=answer,
    )


@dataclass(slots=True)
class ToolRegistry:
    specs: dict[str, ToolSpec]
    handlers: dict[str, ToolHandler]

    def describe_for_prompt(self) -> str:
        lines = []
        for name in sorted(self.specs):
            spec = self.specs[name]
            lines.append(f"- {spec.name}: {spec.description}")
            lines.append(f"  input_schema: {spec.input_schema}")
        return "\n".join(lines)

    def execute(self, task: PublicTask, action: str, action_input: dict[str, Any]) -> ToolExecutionResult:
        if action not in self.handlers:
            raise KeyError(f"Unknown tool: {action}")
        return self.handlers[action](task, action_input)

    def has_tool(self, action: str) -> bool:
        """Check if a tool is registered (either in handlers or specs)."""
        return action in self.handlers or action in self.specs


def create_default_tool_registry(include_fork_subagent: bool = True) -> ToolRegistry:
    """Create the default tool registry.
    
    Args:
        include_fork_subagent: Whether to include the fork_subagent tool in specs.
            Should be False for SubAgents to prevent nested forking.
    """
    specs: dict[str, ToolSpec] = {
        "answer": ToolSpec(
            name="answer",
            description="Submit the final answer table. This is the only valid terminating action.",
            input_schema={
                "columns": ["column_name"],
                "rows": [["value_1"]],
            },
        ),
        "calculate_math": ToolSpec(
            name="calculate_math",
            description="Evaluate simple mathematical expressions (add, subtract, multiply, divide, modulo, power). Use for arithmetic calculations instead of writing code.",
            input_schema={"expression": "2 + 3 * 4"},
        ),
        "execute_context_sql": ToolSpec(
            name="execute_context_sql",
            description=(
                "Run a read-only SQL query against a sqlite/db file inside context.\n\n"
                + SQL_SAFEGUARD_RULES + "\n\n"
                + SQL_FAILURE_CASES
            ),
            input_schema={"path": "relative/path/to/file.sqlite", "sql": "SELECT ...", "limit": 200},
        ),
        "execute_python": ToolSpec(
            name="execute_python",
            description=(
                "Execute arbitrary Python code with the task context directory as the "
                "working directory. The tool returns the code's captured stdout as `output`. "
                f"The execution timeout is fixed at {EXECUTE_PYTHON_TIMEOUT_SECONDS} seconds.\n\n"
                + PYTHON_CALC_SAFEGUARDS
            ),
            input_schema={
                "code": "import os\nprint(sorted(os.listdir('.')))",
            },
        ),
        "inspect_sqlite_schema": ToolSpec(
            name="inspect_sqlite_schema",
            description="Inspect tables and columns in a sqlite/db file inside context.",
            input_schema={"path": "relative/path/to/file.sqlite"},
        ),
        "list_context": ToolSpec(
            name="list_context",
            description="List files and directories available under context.",
            input_schema={"max_depth": 4},
        ),
        "read_csv": ToolSpec(
            name="read_csv",
            description="Read a preview of a CSV file inside context.",
            input_schema={"path": "relative/path/to/file.csv", "max_rows": 20},
        ),
        "read_doc": ToolSpec(
            name="read_doc",
            description=(
                "Read a text-like document inside context. "
                "IMPORTANT: When the question mentions \"normal/abnormal/severe/high/low\" or other qualitative thresholds, "
                "you MUST read knowledge.md FIRST to find the exact numeric definitions before writing any SQL filter or Python code."
            ),
            input_schema={"path": "relative/path/to/file.md", "max_chars": 4000},
        ),
        "read_json": ToolSpec(
            name="read_json",
            description="Read a preview of a JSON file inside context.",
            input_schema={"path": "relative/path/to/file.json", "max_chars": 4000},
        ),
    }
    
    # Only include fork_subagent in specs if requested (for Orchestrator, not SubAgents)
    if include_fork_subagent:
        specs["fork_subagent"] = ToolSpec(
            name="fork_subagent",
            description=(
                "Fork a sub-agent to handle a specific sub-task in parallel. "
                "Provide detailed instructions: specify WHAT to find and HOW. "
                "The sub-agent inherits your full conversation context and works independently."
            ),
            input_schema={
                "task_description": "(REQUIRED) Specific, actionable instruction (e.g., 'find patients with creatinine > 1.2')",
                "task_context": "(REQUIRED) Key info: data format, filtering criteria, parsing strategy, file paths",
                "expected_output": "(REQUIRED) Exact format of expected result (e.g., 'Python list of patient IDs')",
            },
        )
    
    handlers = {
        "answer": _answer,
        "calculate_math": _calculate_math,
        "execute_context_sql": _execute_context_sql,
        "execute_python": _execute_python,
        "inspect_sqlite_schema": _inspect_sqlite_schema,
        "list_context": _list_context,
        "read_csv": _read_csv,
        "read_doc": _read_doc,
        "read_json": _read_json,
    }
    return ToolRegistry(specs=specs, handlers=handlers)
