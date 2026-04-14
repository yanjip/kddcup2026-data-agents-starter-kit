from __future__ import annotations

import json

from data_agent_baseline.benchmark.schema import PublicTask



# =============================================================================
# SubAgent System Prompts
# =============================================================================

SUBAGENT_RESPONSE_EXAMPLES = """
Example response when exploring data:
```json
{"thought":"Plan: 1. First, explore the data structure\n2. Identify relevant columns\n3. Filter and analyze\n\nBlocker: None\n\nI'll start by listing the available files.","action":"list_context","action_input":{"max_depth": 2}}
```

Example response when querying data:
```json
{"thought":"Plan: 1. ✓ Listed files\n2. Now read the relevant CSV file\n3. Filter based on conditions\n\nBlocker: None\n\nI'll read the data file to examine its contents.","action":"read_doc","action_input":{"path": "context/data.csv"}}
```

Example response for final answer:
```json
{"thought":"Plan: 1. ✓ Read data\n2. ✓ Filtered records\n3. ✓ Calculated results\n\nBlocker: None\n\nI have the final result table.","action":"answer","action_input":{"columns":["first_name","last_name","total_cost"],"rows":[["Sacha","Harrison",866.25]]}}
```
""".strip()


def build_subagent_system_prompt() -> str:
    return """
You are a SubAgent responsible for executing specific data analysis sub-tasks.

## Your Responsibilities

1. **Task Execution**: Focus on completing the assigned sub-task efficiently
2. **Data Exploration**: Use available tools to explore and analyze data
3. **Tool Usage**: Call appropriate tools to read, filter, and process data
4. **Result Submission**: Submit your final answer using the `answer` tool

## Answer Tool Format

When you have the final result, you MUST use the `answer` tool with this exact JSON structure:

```json
{"action":"answer","action_input":{"columns":["column1","column2"],"rows":[["value1","value2"],["value3","value4"]]}}
```

**CRITICAL RULES for answer tool:**
1. `columns` must be an array of strings
2. `rows` must be an array of arrays (each inner array is one row)
3. All arrays must be properly closed with matching brackets `]` 
4. String values must be in double quotes
5. Numeric values should NOT be wrapped in quotes
6. The entire JSON must be valid - check your brackets carefully

## Core Rules

1. First, analyze the task and create a clear plan before executing any actions
2. Use the available tools to explore and analyze data
3. Always call the `answer` tool when you have the final result
4. Return exactly one JSON object with keys `thought`, `action`, and `action_input`
5. Always wrap the JSON object in exactly one fenced code block starting with ```json and ending with ```
6. Do not output any text before or after the fenced JSON block
7. When returning numerical results in the `answer` tool, use the full precision without rounding
""".strip()


# =============================================================================
# Orchestrator System Prompts
# =============================================================================

ORCHESTRATOR_RESPONSE_EXAMPLES = """
Example response when forking a sub-agent:
```json
{"thought":"Plan: 1. Explore data structure in parallel using sub-agents\\n2. Combine results for final analysis\\nBlocker: None\\nAssumption: Independent data exploration can happen in parallel\\n\\nI'll fork two sub-agents to explore different aspects of the data.","action":"fork_subagent","action_input":{"task_description":"Explore the customers table schema and sample data","task_context":"List files in context/, read knowledge.md for schema info","expected_output":"Schema details and sample rows from customers table"}}
```

Example response when handling fork result:
```json
{"thought":"Sub-agent 'explorer1' completed successfully. Got schema for customers table.\\nPlan: 1. ✓ Schema exploration\\n2. Query customers table\\n3. Join with orders\\nBlocker: None\\nAssumption: None\\n\\nNow I have the schema, I'll proceed with querying.","action":"read_doc","action_input":{"path":"context/customers.csv"}}
```

Example response for final answer:
```json
{"thought":"Plan: 1. ✓ Explored data\n2. ✓ Queried tables\n3. ✓ Combined results\nBlocker: None\nAssumption: Verified\n\nI have the final result table.","action":"answer","action_input":{"columns":["department","avg_salary"],"rows":[["Engineering",95000],["Sales",72000]]}}
```
""".strip()





def build_orchestrator_system_prompt(max_subagents: int, difficulty: str = "medium") -> str:
    # Determine if this is a hard/extreme difficulty task requiring mandatory subagent usage
    is_hard_or_extreme = difficulty.lower() in ("hard", "extreme")

    mandatory_subagent_section_template = """
## ⚠️ MANDATORY SUBAGENT USAGE REQUIRED ⚠️

This is a HIGH DIFFICULTY task (difficulty: {difficulty}). You **MUST** use sub-agents to solve this task.

### Requirements:
1. **ALWAYS fork at least 1 sub-agent** - Do not attempt to solve this task entirely by yourself
2. **Break down the problem** - Identify at least 2-3 independent sub-tasks that can run in parallel
3. **Delegate effectively** - Use fork_subagent for:
   - Exploring different data sources simultaneously
   - Investigating multiple hypotheses in parallel
   - Handling complex multi-step transformations
   - Cross-validating results across different approaches

### Example approach for hard tasks:
```json
{{"thought":"This is a hard task requiring parallel exploration. I'll fork 2 sub-agents:\n1. Sub-agent A: Explore data source X and find relevant records\n2. Sub-agent B: Explore data source Y and find relevant records\nThen I'll combine their results.","action":"fork_subagent","action_input":{{"task_description":"Explore data source X","task_context":"Focus on finding...","expected_output":"List of relevant records from X"}}}}
```

### Important:
- You have {max_subagents} sub-agents available - use them wisely
- Sub-agents inherit your full context, so they know what you've learned
- After sub-agents complete, synthesize their results for the final answer
- **Failure to use sub-agents on hard/extreme tasks will likely result in incomplete solutions**
""".strip()

    mandatory_subagent_section = mandatory_subagent_section_template.format(difficulty=difficulty, max_subagents=max_subagents) if is_hard_or_extreme else ""

    return f"""
You are the Orchestrator Agent responsible for coordinating complex data analysis tasks.

## Your Responsibilities

1. **Task Analysis**: Understand the overall task and break it down into manageable sub-tasks
2. **Planning**: Create a clear execution plan with independent work streams that can run in parallel
3. **Delegation**: Fork sub-agents to handle specific sub-tasks when appropriate
4. **Synthesis**: Combine results from sub-agents to produce the final answer

## Fork Mechanism

When you identify a sub-task suitable for parallel execution, you can fork a sub-agent by returning:
```json
{{"thought":"...","action":"fork_subagent","action_input":{{"task_description":"...","task_context":"...","expected_output":"..."}}}}
```

The sub-agent will inherit your complete context including:
- System prompt with tool definitions
- Full conversation history
- All tool registrations

{mandatory_subagent_section}

## Handling Unstructured Data

Some tasks contain data in unstructured formats (e.g., text documents, markdown files) rather than structured databases. When you encounter such tasks:

1. **Use `read_doc` to read the full content** of text/markdown files
2. **Use `execute_python` with regex or string parsing** to extract structured information
3. **Do NOT assume SQLite databases exist** - Check `list_context` first to see what files are actually available
4. **For complex parsing tasks**, consider using sub-agents to handle different document types in parallel

## Output Formatting Rules
1. **Preserve Individual Fields**: When asked for "full name" or similar combined attributes, always return the individual components (e.g. first_name and last_name) as separate columns in your final output.
2. **Multi-Column Output**: For tasks involving multiple attributes or dimensions, return results with distinct columns for each attribute rather than combining them into a single column.
3. **Structured Results**: Use the `answer` tool with appropriate column names that match the data schema (e.g. `["first_name", "last_name"]` instead of `["full_name"]`).
4. **Clarity**: If a task asks for "full name", you may include both the combined full name and individual components if it adds value, but prioritize returning the individual fields as separate columns.
5. **Minimal Output**: Only return the columns explicitly requested in the task. Do not include intermediate columns or additional information unless specifically asked for. If the task asks for a ratio or calculated value, return only that final result column.
6. **Column Name Mapping**: Use the column names specified in the task or data schema.
7. **Strict Focus**: Even if you need intermediate columns for calculations, do not include them in the final answer. Only include the exact columns requested in the task description.

## Core Rules

1. First, analyze the task and create a clear plan before executing any actions
2. Identify independent work streams that can be executed in parallel via sub-agents
3. Sub-agents are especially useful for:
   - Independent data exploration tasks
   - Parallel hypothesis testing
   - Multiple independent queries
   - Complex transformations that can be split

4. Maximum {max_subagents} sub-agents can be active at once
5. Always call the `answer` tool when you have the final result
6. Return exactly one JSON object with keys `thought`, `action`, and `action_input`
7. Always wrap the JSON object in exactly one fenced code block starting with ```json and ending with ```
8. Do not output any text before or after the fenced JSON block
9. When returning numerical results in the `answer` tool, use the full precision of the calculated value without rounding or formatting. Do not wrap numbers in quotes.
""".strip()


def build_task_prompt(task: PublicTask) -> str:
    return (
        f"Question: {task.question}\n"
        "All tool file paths are relative to the task context directory. "
        "When you have the final table, call the `answer` tool."
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
    original_reasoning: str = ""
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
1. Work backwards from the answer to verify it against the data
2. Find evidence that supports or contradicts the answer
3. Return your verification result using the `answer` tool

Use the available tools to explore the data and verify the answer.
""".strip()


def build_verification_observation_prompt(observation: dict[str, object]) -> str:
    """构建验证Agent的观察提示词"""
    rendered = json.dumps(observation, ensure_ascii=False, indent=2)
    return f"Verification Observation:\n{rendered}"


def integrate_verification_result(
    original_prompt: str,
    verification_result: dict[str, Any]
) -> str:
    """
    将验证结果整合到主Agent的prompt中
    
    当验证失败时，使用此函数生成反馈给主Agent的提示
    """
    is_valid = verification_result.get('is_valid', False)
    reasoning = verification_result.get('reasoning', '')
    suggested_fix = verification_result.get('suggested_fix', '')
    
    if is_valid:
        return original_prompt
    
    feedback = f"""

## ⚠️ PREVIOUS ANSWER FAILED VERIFICATION

Your previous answer was rejected by the verification system.

**Reason**: {reasoning}
"""
    
    if suggested_fix:
        feedback += f"\n**Suggested Fix**: {suggested_fix}\n"
    
    feedback += """
Please reconsider your approach and provide a corrected answer.
Think about:
1. Did you use the correct data sources?
2. Did you interpret the question correctly?
3. Are there any edge cases you missed?

"""
    
    return original_prompt + feedback
