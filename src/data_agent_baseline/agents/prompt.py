from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from data_agent_baseline.benchmark.schema import PublicTask


# =============================================================================
# Shared Prompt Modules
# =============================================================================

ANSWER_FORMAT_PROMPT = """
## Answer Tool Format

When you have the final result, use the `answer` tool with this exact JSON shape:
```json
{"action":"answer","action_input":{"columns":["column1","column2"],"rows":[["value1","value2"]]}}
```

Rules:
1. `columns` must be a non-empty array of strings.
2. `rows` must be an array of arrays, and every row must match the number of columns.
3. Numeric values should not be quoted.
4. Use full precision for numeric answers; do not round unless the task explicitly asks.
5. Return exactly one JSON object with keys `thought`, `action`, and `action_input`.
6. Wrap the JSON object in exactly one ```json fenced block and output no extra text.
""".strip()


CORE_PROMPT = """
## Core Workflow

1. Understand the question literally.
2. Inspect available files before assuming a data source exists.
3. ALL computation MUST be done via `execute_python`. Never do arithmetic, counting, filtering, date/time comparison, or aggregation in your head.
4. For time/date comparison, parse values into numeric/datetime values. Never compare time strings directly.
5. If a tool call fails, read the error and fix the input format or switch strategy. If the same code/tool fails twice, the third attempt MUST use a different approach.
6. For relationship counts, use `DISTINCT` or Python `set()` to avoid double-counting.
7. If the task expects one value or a small result set, verify your output is not returning too many rows.
8. Keep empty/null values as-is. Never substitute missing values with data from another column.
9. All file paths are relative to the task context directory. Use exact paths from `list_context`; do not add a `context/` prefix.
10. Once you have uniquely identified the needed record/entity set and computed the requested final value/table, call `answer` immediately. Do not keep searching for fields the question did not request.
11. If a later broad search or fragile regex contradicts an earlier precise lookup, return to the precise evidence instead of overwriting it.
""".strip()


OUTPUT_PROMPT = """
## Final Output Discipline

1. Before calling `answer`, decide the exact output columns requested by the question.
2. Return only those requested columns. Do not include helper/debug columns such as IDs, dates, account IDs, balances, counts, or intermediate values unless explicitly asked.
3. "List all X" means return only the X field, not the entire row.
4. Multiple metrics usually belong horizontally in one row, not pivoted into multiple metric-name rows.
5. If the question asks for "type of X and total value", group by the requested type level, not by lower-level descriptions unless explicitly asked.
6. Keep null/empty optional attributes as null/empty; do not fill them from other columns.
7. When asked for "full name" or similar combined attributes and the data has separate name fields, return individual components such as `first_name` and `last_name` as separate columns.
8. For "list/show all records/transactions/people/customers/events that..." tasks, keep enough entity-identifying columns to identify each returned record, plus the requested value/status fields. Do not collapse a record list to only a bare amount/value if the row identity would be lost.
9. For "tally", "summarize", "count by", "types/categories/elements", or similar wording, decide the output grain before answering: distinct values, counts per value, totals per value, or per-record details. Do not return per-record detail rows when the question asks for a category/element summary.
10. Before submitting, verify: only appropriate requested/identifying columns, no calculation-only columns, correct grouping column, exact task/schema column terminology, and nulls preserved as-is.
""".strip()


KNOWLEDGE_PROMPT = """
## knowledge.md Usage

If `knowledge.md` exists, read it early and use it as the first authority for schemas, definitions, metric formulas, thresholds, ambiguity notes, and exemplar SQL.

Important:
1. Follow explicit knowledge.md formulas, field mappings, thresholds, and examples exactly.
2. Extract every relevant definition, table schema detail, metric formula, constraint, ambiguity resolution, and exemplar SQL. Do not summarize away potentially useful details.
3. Pay special attention to Exemplar Use Cases, Ambiguity Resolution, Constraints & Conventions, and Metric Definitions.
4. knowledge.md has absolute authority. If it defines `cost` as `AVG(cost)`, a threshold as `UA > 8.0`, or "severe" as `Thrombosis = 2`, use exactly that and do not broaden it.
5. If knowledge.md does not define a needed threshold or term after one thorough check, do not keep searching. Infer from data or standard domain knowledge and proceed.
6. Map task keywords to knowledge.md fields before choosing columns such as cost/amount/spent/type/status/rank.
7. Do not delegate knowledge.md reading when you are the orchestrator; use it to form your own plan.
""".strip()


TABULAR_PROMPT = """
## Tabular Data Rules

1. Load headers and a sample before writing final filters.
2. Use exact column names from the data or knowledge.md.
3. For joins across files, verify key uniqueness and whether optional attributes need LEFT JOIN behavior.
4. For counts and relationship tables, use `DISTINCT` or Python `set()` when duplicates are possible.
5. Print row counts after each major filter so unexpected empty or huge result sets are caught before answering.
6. Do not compute final aggregates from previews, samples, truncated tool output, or default-limited query results. Load/query the full source data.
""".strip()


CSV_PROMPT = """
## CSV/TSV/XLSX Rules

1. Use Python/pandas or csv readers for filtering and aggregation; do not rely only on previews.
2. Preserve types carefully: IDs may need to stay as strings, while metrics should be numeric.
3. Check date formats before filtering by month/year; parse dates rather than comparing arbitrary strings.
4. If the answer seems empty, inspect available date ranges and alternate files before concluding no records exist.
5. For final calculations, read the complete file with Python/pandas/csv. A preview is only for schema discovery.
""".strip()


JSON_PROMPT = """
## JSON Rules

1. Use Python's `json` module to load JSON; do not parse JSON with ad hoc string matching.
2. Inspect whether records live at the top level or under keys such as `records`, `data`, or `table`.
3. If JSON mirrors a table, preserve nulls and field names exactly.
4. When JSON and another structured source disagree, compare coverage and prefer the source with complete records for the requested fields.
""".strip()


DOCUMENT_PROMPT = """
## Document/Text Rules

1. For markdown/text data, use `execute_python` to scan and extract relevant records. `read_doc` previews may be truncated.
2. Search targeted keywords first, then parse nearby context into structured records.
3. For narrative medical/lab records, extract patient IDs, dates, values, and descriptors line by line; do not rely on one fragile regex.
4. If a term or threshold is absent from knowledge.md and documents after one thorough pass, infer and proceed instead of burning steps.
""".strip()


LARGE_DOCUMENT_PROMPT = """
## Large Document Handling

This task likely involves large text/markdown files.

1. Use `execute_python` to get file sizes, line counts, and keyword hits.
2. Do not page through large files with repeated `read_doc`; process them with Python line slicing/search.
3. For hard/extreme tasks, fork sub-agents for independent chunks or independent hypotheses when useful.
4. Focus on records matching the query criteria rather than reading the entire document into the prompt.
5. For narrative documents such as medical records or lab reports, process line by line and extract structured patient IDs, dates, numeric values, and descriptors. Do not rely on one fragile regex over a truncated preview.
""".strip()


RELATIONAL_LOGIC_PROMPT = """
## Relational Logic Safeguards

### AND vs OR
- When the question says "have X and Y", decide whether X and Y can coexist on a single row.
- If they are mutually exclusive on one row, find entities that have BOTH via self-join, subquery, or GROUP BY/HAVING COUNT(DISTINCT ...) >= 2.
- Do not use `WHERE field = X OR field = Y` when the target entity must satisfy both conditions.
- Example: "bonds that have phosphorus and nitrogen as atom elements" means the SAME bond must connect one phosphorus atom and one nitrogen atom, not merely a molecule containing any P or N bond.

### NULL Preservation
- For "list/show X and optional Y", keep records with missing Y by using LEFT JOIN behavior.
- Put filters on the optional/right table inside the join condition, not in WHERE, or NULL rows will be dropped.
""".strip()


AGGREGATION_PROMPT = """
## Aggregation, Ratio, and Percentage Rules

1. Before dividing, print numerator and denominator separately.
2. "How many times is A compared to B" means A / B, not B / A.
3. For AVG/SUM/COUNT/ratio, print intermediate values and sanity-check the order of magnitude.
4. Percentages must be between 0 and 100 unless the task explicitly asks for percent change that can exceed 100.
5. Decide the aggregation grain: per row, per entity, per customer, per month, or whole dataset. Do not change grain silently.
6. For "per unit" wording, compute or identify a unit price/rate such as total price divided by quantity/amount unless knowledge.md defines a different unit-price field.
7. If two plausible formulas exist, compute both, compare against the wording/knowledge.md, and explain the chosen one in `thought` before answering.
""".strip()


RANKING_PROMPT = """
## Ranking and Nth-Row Rules

1. For "ranked second", "2nd", "highest", "lowest", "top", or "last", print the surrounding top 5 rows with all relevant ranking fields.
2. Verify the field meaning before selecting the row. Example: in Formula 1 data, `rank` can mean fastest-lap rank, while `positionOrder` means finishing order.
3. Check ties before returning only one row.
4. Never blindly use `LIMIT 1 OFFSET N` without verifying surrounding rows.
5. Compute top/highest/lowest over the complete filtered candidate set, not over a preview or a partial list.
""".strip()


MINMAX_PROMPT = """
## Minimum/Maximum Rules

1. When asking for a real minimum/maximum, check whether 0 or NULL is a placeholder/missing/unstarted value.
2. Do not drop 0 values from AVG/SUM/COUNT or ordinary filters just because they look suspicious. Exclude 0 only when the question, knowledge.md, schema, or data documentation shows that 0 means missing/placeholder/unstarted.
3. If many rows tie at 0/NULL for a real "lowest cost/cheapest/smallest" question, inspect whether 0/NULL is meaningful before answering.
4. If the question explicitly asks for absence/no activity/unstarted records, keep 0/NULL as meaningful.
5. Return all genuine ties, not just one row.
""".strip()


THRESHOLD_PROMPT = """
## Threshold and Qualifier Rules

1. For words like normal, abnormal, severe, high, low, active, legal, or valid, look for exact definitions in knowledge.md first.
2. If knowledge.md lacks the definition after one thorough check, infer from domain knowledge or data distribution and proceed.
3. Print the chosen threshold/rule and the count of records before and after applying it.
4. For patient/lab tasks, count DISTINCT patient IDs unless the task explicitly asks for records/tests.
""".strip()


FIELD_MAPPING_PROMPT = """
## Field Mapping and Entity Return Rules

1. For terms like cost, amount, spent, fee, price, total, type, category, status, rank, and legal, prefer explicit knowledge.md definitions first, exact column-name matches second, and semantic guesses last.
2. Do not equate similar fields by intuition (`spent` is not automatically `cost`; `category` is not automatically `type`).
3. Natural-language entities usually want human-readable fields:
   - "the comment" -> `Text`, not comment Id
   - "the post" -> `Title`, not post Id
   - "the event" -> `event_name`, not event_id
   - "the user" -> `DisplayName` or name fields, not user Id
4. Capitalized proper nouns and official names in the question usually need exact matching against name/title fields before broader semantic expansion. For example, first test whether "European Grand Prix" is a race/event name, not just a geographic phrase.
5. For "type of X" questions, map the type at the X level using knowledge.md/schema before grouping. For example, "type of expenses" may refer to an event/expense business type, not necessarily a lower-level budget category such as Food or Advertisement.
6. Return primary keys only when the question explicitly asks for id/identifier.
""".strip()


SQL_PROMPT = """
## SQL/SQLite Rules

1. Inspect schema before writing queries.
2. Use read-only SELECT/WITH/PRAGMA queries.
3. Prefer SQL for joins/aggregation when tables are in a database, then optionally verify with pandas/raw Python.
4. Use GROUP BY/HAVING for entity-level conditions over multiple rows.
5. Use LEFT JOIN for optional attributes that must still appear in list/show answers.
""".strip()


SELF_CONSISTENCY_PROMPT = """
## Self-Consistency Voting

Before final answer on non-trivial aggregation, join, ranking, ratio, min/max, or threshold tasks, run two-method verification:
1. Method A: compute the answer using the primary approach, such as SQL or pandas.
2. Method B: in a separate `execute_python` call, re-compute the same answer using a different approach.
3. Both methods must print their results so they can be compared.
4. If results differ, investigate the filter, grain, field mapping, and knowledge.md constraints before answering.
5. Only call `answer` after the independent check agrees, unless the task is a simple direct lookup.
""".strip()


F1_PROMPT = """
## Formula 1 Data Reminders

1. `rank` and `positionOrder` can mean different things. For "ranked second", inspect both fields and knowledge.md wording before choosing.
2. Qualifying `q1/q2/q3` times are strings; parse times to seconds for matching or nearest-time logic.
3. Race `round` is not necessarily "track number"; inspect available driver/race/standing fields before assuming.
4. Grand Prix phrases are often exact race names. Filter `races.name = '<Name> Grand Prix'` first when the wording says "all <Name> Grand Prix races"; do not reinterpret the phrase as all races in a region unless exact-name matching fails or the question explicitly asks for countries/regions.
5. For time questions, print nearby rows and return all valid ties/near matches required by the wording.
""".strip()


COMMANDER_PROMPT = """
## Commander/Card Data Reminder

For card questions, do not assume `edhrecRank` means commander legal. Inspect explicit format/legal/status fields or code patterns in the data. If using `printings`/set codes for Commander, verify content-warning counts against that exact filtered denominator.
""".strip()


# =============================================================================
# Dynamic Guidance Selection
# =============================================================================


@dataclass(frozen=True, slots=True)
class ContextProfile:
    suffixes: set[str]
    has_knowledge: bool
    has_document: bool
    has_large_doc: bool


def _context_profile(task: PublicTask) -> ContextProfile:
    suffixes: set[str] = set()
    has_knowledge = False
    has_document = False
    has_large_doc = False

    if not task.context_dir.exists():
        return ContextProfile(
            suffixes=suffixes,
            has_knowledge=False,
            has_document=False,
            has_large_doc=False,
        )

    for path in task.context_dir.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        suffixes.add(suffix)
        if path.name.lower() == "knowledge.md":
            has_knowledge = True
        elif suffix in {".md", ".txt"}:
            has_document = True
        if suffix in {".md", ".txt"} and path.stat().st_size > 50_000:
            has_large_doc = True

    return ContextProfile(
        suffixes=suffixes,
        has_knowledge=has_knowledge,
        has_document=has_document,
        has_large_doc=has_large_doc,
    )


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    for keyword in keywords:
        if " " in keyword:
            if keyword in text:
                return True
            continue
        if re.search(rf"\b{re.escape(keyword)}\b", text):
            return True
    return False


def _read_memory() -> str:
    memory_path = Path(__file__).resolve().parents[3] / "memory.md"
    if not memory_path.exists():
        return ""
    return memory_path.read_text(encoding="utf-8").strip()


def _extract_memory_case(memory_text: str, case_letter: str) -> str:
    pattern = rf"^### Case {case_letter}:.*?(?=^### Case [A-Z]:|\Z)"
    match = re.search(pattern, memory_text, flags=re.MULTILINE | re.DOTALL)
    return match.group(0).strip() if match else ""


def _memory_common_rules(memory_text: str) -> str:
    marker = "\n## 8. SQL Logic Failures"
    if marker not in memory_text:
        return memory_text
    return memory_text.split(marker, 1)[0].strip()


def _selected_memory_common_rules() -> str:
    memory_text = _read_memory()
    if not memory_text:
        return ""

    common_rules = _memory_common_rules(memory_text)
    if not common_rules:
        return ""
    return "## Historical Failure Patterns (read before solving)\n\n" + common_rules


def _selected_memory_cases(signal_text: str) -> str:
    memory_text = _read_memory()
    if not memory_text:
        return ""

    selected_case_letters: list[str] = []
    if _has_any(signal_text, ("both", "phosphorus", "nitrogen", "bond")):
        selected_case_letters.append("A")
    if _has_any(signal_text, ("average", "avg", "monthly", "consumption")):
        selected_case_letters.append("B")
    if _has_any(signal_text, ("percentage", "ratio", "how many times", "compared to")):
        selected_case_letters.extend(["C", "F"])
    if _has_any(signal_text, ("funding", "null", "list the names", "left join")):
        selected_case_letters.append("D")
    if _has_any(signal_text, ("rank", "ranked", "second", "2nd", "highest", "top")):
        selected_case_letters.append("G")
    if _has_any(signal_text, ("lowest", "minimum", "cheapest", "smallest")):
        selected_case_letters.append("H")
    if _has_any(signal_text, ("withdrawals", "transactions", "consumption status")):
        selected_case_letters.append("I")
    if _has_any(signal_text, ("average weight", "weight_kg", "female superheroes")):
        selected_case_letters.append("J")
    if _has_any(signal_text, ("per unit", "unit price", "product id")):
        selected_case_letters.append("K")
    if _has_any(signal_text, ("european grand prix", "grand prix races")):
        selected_case_letters.append("L")

    selected_cases = [
        _extract_memory_case(memory_text, case_letter)
        for case_letter in dict.fromkeys(selected_case_letters)
    ]
    selected_cases = [case for case in selected_cases if case]
    if not selected_cases:
        return ""
    return "## Historical Failure Patterns - Triggered Real Cases\n\n" + "\n\n".join(selected_cases)


def build_runtime_step_guidance(
    thought: str,
    action: str,
    action_input: dict[str, Any],
) -> str:
    signal_text = " ".join(
        (
            thought,
            action,
            json.dumps(action_input, ensure_ascii=False, sort_keys=True),
        )
    ).lower()

    sections: list[str] = []

    if _has_any(signal_text, ("grand prix", "qualifying", "q1", "q2", "q3", "driver", "race")):
        sections.append(F1_PROMPT)

    if _has_any(signal_text, ("commander", "content warning", "card", "legal status")):
        sections.append(COMMANDER_PROMPT)

    memory_cases = _selected_memory_cases(signal_text)
    if memory_cases:
        sections.append(memory_cases)

    if not sections:
        return ""
    return "## Runtime Reminder Based on Your Last Thought/Action\n\n" + "\n\n".join(dict.fromkeys(sections))


def build_dynamic_guidance(task: PublicTask) -> str:
    profile = _context_profile(task)
    question_lower = task.question.lower()
    suffixes = profile.suffixes

    sections: list[str] = [
        OUTPUT_PROMPT,
        CORE_PROMPT,
        RELATIONAL_LOGIC_PROMPT,
        AGGREGATION_PROMPT,
        RANKING_PROMPT,
        MINMAX_PROMPT,
        THRESHOLD_PROMPT,
        FIELD_MAPPING_PROMPT,
        SELF_CONSISTENCY_PROMPT,
    ]

    memory_common = _selected_memory_common_rules()
    if memory_common:
        sections.append(memory_common)

    if profile.has_knowledge:
        sections.append(KNOWLEDGE_PROMPT)

    if suffixes & {".csv", ".tsv", ".xlsx", ".xls"}:
        sections.extend([TABULAR_PROMPT, CSV_PROMPT])

    if suffixes & {".json"}:
        sections.append(JSON_PROMPT)

    if suffixes & {".db", ".sqlite", ".sqlite3"}:
        sections.append(SQL_PROMPT)

    if profile.has_document:
        sections.append(DOCUMENT_PROMPT)

    if profile.has_large_doc or (
        task.difficulty.lower() in {"hard", "extreme"}
        and _has_any(
            question_lower,
            ("patient", "laboratory", "medical record", "markdown", "text file", "document"),
        )
    ):
        sections.append(LARGE_DOCUMENT_PROMPT)

    if _has_any(question_lower, ("grand prix", "qualifying", "q1", "q2", "q3", "driver", "race")):
        sections.append(F1_PROMPT)

    if _has_any(question_lower, ("commander", "content warning", "card", "legal status")):
        sections.append(COMMANDER_PROMPT)

    memory = _selected_memory_cases(question_lower)
    if memory:
        sections.append(memory)

    return "\n\n".join(dict.fromkeys(sections))


# =============================================================================
# SubAgent System Prompts
# =============================================================================


SUBAGENT_RESPONSE_EXAMPLES = """
Example response when exploring data:
```json
{"thought":"Plan: 1. First, explore the data structure\\n2. Identify relevant columns\\n3. Filter and analyze\\n\\nBlocker: None\\n\\nI'll start by listing the available files.","action":"list_context","action_input":{"max_depth":2}}
```

Example response for final answer:
```json
{"thought":"Plan: 1. ✓ Read data\\n2. ✓ Filtered records\\n3. ✓ Calculated results\\n\\nBlocker: None\\n\\nI have the final result table with only the requested columns.","action":"answer","action_input":{"columns":["first_name","last_name","total_cost"],"rows":[["Sacha","Harrison",866.25]]}}
```
""".strip()


def build_subagent_system_prompt() -> str:
    return f"""
You are a SubAgent responsible for executing a specific data analysis sub-task.

## Responsibilities

1. Focus on the assigned sub-task.
2. Explore files and data with the available tools.
3. Compute using tools, not mental math.
4. Submit final results with the `answer` tool.
5. You do NOT have `fork_subagent`; never delegate.

{ANSWER_FORMAT_PROMPT}

Task-specific guidance will be provided in the user prompt based on available files and question wording.

{SUBAGENT_RESPONSE_EXAMPLES}
""".strip()


# =============================================================================
# Orchestrator System Prompts
# =============================================================================


ORCHESTRATOR_RESPONSE_EXAMPLES = """
Example response when forking a sub-agent:
```json
{"thought":"Plan: 1. Explore data structure in parallel using sub-agents\\n2. Combine results for final analysis\\nBlocker: None\\nAssumption: Independent exploration can happen in parallel","action":"fork_subagent","action_input":{"task_description":"Explore the customers table schema and sample data","task_context":"List files in context and inspect relevant schema/data","expected_output":"Schema details and sample rows from customers table"}}
```

Example response for final answer:
```json
{"thought":"Plan: 1. ✓ Explored data\\n2. ✓ Queried tables\\n3. ✓ Combined results\\nBlocker: None\\nAssumption: Verified","action":"answer","action_input":{"columns":["department","avg_salary"],"rows":[["Engineering",95000],["Sales",72000]]}}
```
""".strip()


def build_orchestrator_system_prompt(max_subagents: int, difficulty: str = "medium") -> str:
    is_hard_or_extreme = difficulty.lower() in ("hard", "extreme")

    mandatory_subagent_section = ""
    if is_hard_or_extreme:
        mandatory_subagent_section = f"""
## Mandatory SubAgent Usage

This is a high-difficulty task (difficulty: {difficulty}). Use at least one sub-agent when there are independent data sources, large documents, competing hypotheses, or cross-validation work streams.

You have {max_subagents} sub-agents available. Fork them in sequence; the runtime will execute queued sub-agents in parallel.
""".strip()

    return f"""
You are the Orchestrator Agent responsible for coordinating data analysis tasks.

## Responsibilities

1. Understand the task and identify the needed data sources.
2. Plan the work and delegate independent sub-tasks when useful.
3. Synthesize tool and sub-agent results into one final answer.
4. Submit final results with the `answer` tool.

## Fork Mechanism

When a sub-task can run independently, call:
```json
{{"thought":"...","action":"fork_subagent","action_input":{{"task_description":"...","task_context":"...","expected_output":"..."}}}}
```

{mandatory_subagent_section}

{ANSWER_FORMAT_PROMPT}

Task-specific guidance will be provided in the user prompt based on available files and question wording.

{ORCHESTRATOR_RESPONSE_EXAMPLES}
""".strip()


def build_task_prompt(task: PublicTask) -> str:
    dynamic_guidance = build_dynamic_guidance(task)
    return (
        f"Question: {task.question}\n"
        "All tool file paths are relative to the task context directory. "
        "When you have the final table, call the `answer` tool.\n\n"
        f"## Dynamic Task Guidance\n\n{dynamic_guidance}"
    )


def build_observation_prompt(observation: dict[str, object]) -> str:
    rendered = json.dumps(observation, ensure_ascii=False, indent=2)
    return f"Observation:\n{rendered}"


# =============================================================================
# Verification Agent Prompts
# =============================================================================


def build_verification_task_prompt(
    task: PublicTask,
    proposed_answer: dict[str, Any],
    original_reasoning: str = "",
) -> str:
    """构建验证任务的提示词"""
    reasoning_section = f"\n## Original Reasoning\n{original_reasoning}\n" if original_reasoning else ""

    return f"""## Verification Task

You are verifying if the proposed answer is correct for the given question.

### Original Question
{task.question}

### Proposed Answer
Columns: {proposed_answer.get('columns', [])}
Rows: {proposed_answer.get('rows', [])}
{reasoning_section}
### Your Goal
1. Work backwards from the answer to verify it against the data.
2. Find evidence that supports or contradicts the answer.
3. Return your verification result using the `answer` tool.

Use the available tools to explore the data and verify the answer.
""".strip()


def build_verification_observation_prompt(observation: dict[str, object]) -> str:
    """构建验证Agent的观察提示词"""
    rendered = json.dumps(observation, ensure_ascii=False, indent=2)
    return f"Verification Observation:\n{rendered}"


def integrate_verification_result(
    original_prompt: str,
    verification_result: dict[str, Any],
) -> str:
    """
    将验证结果整合到主Agent的prompt中

    当验证失败时，使用此函数生成反馈给主Agent的提示
    """
    is_valid = verification_result.get("is_valid", False)
    reasoning = verification_result.get("reasoning", "")
    suggested_fix = verification_result.get("suggested_fix", "")

    if is_valid:
        return original_prompt

    feedback = f"""

## PREVIOUS ANSWER FAILED VERIFICATION

Your previous answer was rejected by the verification system.

Reason: {reasoning}
"""

    if suggested_fix:
        feedback += f"\nSuggested Fix: {suggested_fix}\n"

    feedback += """
Please reconsider your approach and provide a corrected answer.
Think about:
1. Did you use the correct data sources?
2. Did you interpret the question correctly?
3. Are there any edge cases you missed?

"""

    return original_prompt + feedback
