# Failure Patterns — DO NOT REPEAT

## 1. Always map question keywords to knowledge.md FIRST
- Before writing any query, map every keyword in the question to the exact definition in knowledge.md
- Pay special attention to: Metric Definitions, Ambiguity Resolution, and Exemplar Use Cases
- If knowledge.md does not contain the definition you need, **stop searching repeatedly** and infer from the actual data

## 2. Output columns must strictly match the question
- Return ONLY the columns explicitly requested. Never include intermediate calculation columns
- "List all X" means return ONLY the X column, not the entire row
- Multiple metrics must be output horizontally (multiple columns in one row), never pivoted vertically (multiple rows)

## 3. Tool failure requires a strategy switch, not a retry
- **If the same code or tool fails twice, the third attempt MUST use a completely different approach**
- `execute_python` JSON parse error → check for nested double quotes in the code; switch to single quotes or triple quotes
- `execute_python` repeated failures → immediately switch to `read_doc`, `read_csv`, or SQL tools
- Never fall into the "try again and it might work" trap

## 4. Break every question into a three-step sanity check
- Filter: What am I filtering on?
- Compute: What am I calculating? (ratio? sum? average? count?)
- Return: What columns should I output?
- After each step, ask: does this literally match the wording of the question?

## 5. Be suspicious of zeros and anomalies
- When MIN/MAX returns 0, verify whether 0 is a real recorded value or a placeholder/missing-data marker
- When the result row count is unexpectedly high or low, recheck the filtering conditions
- When more than half the returned values are null/empty, recheck the data source and join conditions

## 6. Code style rules for Python snippets
- Prefer single quotes `'` or triple quotes `"""` for strings inside Python code to avoid nested double-quote issues in JSON
- Do NOT use `subprocess.run` with grep to parse JSON files; use Python's built-in `json` module instead

## 7. Large document handling rules
- Do NOT repeatedly use `read_doc` with offset to "page through" a large file
- Use a single `execute_python` call for keyword scanning; if nothing is found, infer from what you already know
- Do NOT spend more than 3 steps on any single large document

## 8. SQL Logic Failures — Real Cases (STUDY THESE)

### Case A: AND vs OR confusion (task_194)
- Question: "What are the bonds that have phosphorus AND nitrogen as their atom elements?"
- WRONG approach: `WHERE element = 'p' OR element = 'n'` → returned 270 bonds (any bond containing P or N)
- CORRECT approach: Find bonds where one atom is P AND the other atom is N on the SAME bond → only 7 bonds
- Lesson: When question says "X and Y" for the same column, use self-join or HAVING COUNT(DISTINCT element) >= 2

### Case B: Aggregation denominator error (task_169)
- Question: "What was the average monthly consumption of customers in SME for the year 2013?"
- WRONG result: 82,027,220 (forgot to divide by number of customers)
- CORRECT result: 459.96 (must be per-customer average, not total)
- Lesson: Always sanity-check aggregation results — if "average per customer" returns millions, something is wrong

### Case C: Percentage formula error (task_408)
- Question: "How much faster in percentage is the champion than the last driver?"
- WRONG result: 1.468% (wrong formula)
- CORRECT result: 0.316% (must use correct base for percentage calculation)
- Lesson: Always print numerator and denominator separately before dividing

### Case D: NULL rows dropped (task_199)
- Question: "List the names and funding types of schools from Riverside..."
- WRONG: Returned only 1 school (the one with non-NULL funding type)
- CORRECT: 6 schools (5 with NULL funding type must still appear)
- Lesson: "List" questions require LEFT JOIN to preserve records with missing optional fields

### Case E: Domain threshold guessing (task_344)
- Question: "Among male patients with normal white blood cells, how many have abnormal fibrinogen?"
- WRONG result: 0 (guessed thresholds incorrectly)
- CORRECT result: 4 (must use exact thresholds from knowledge.md)
- Lesson: NEVER guess medical thresholds — always look up exact values in knowledge.md

### Case F: Ratio numerator/denominator swapped (task_243)
- Question: "How many times is the number of posts compared to votes?"
- WRONG result: 0.103 (inverted the ratio)
- CORRECT result: 0.375 (posts / votes, not votes / posts)
- Lesson: "A compared to B" means A / B. Print both values before dividing

### Case G: Wrong row selected for ranking (task_89)
- Question: "Finish time for the driver who ranked second in 2008 Chinese Grand Prix?"
- WRONG result: +14.925 (picked wrong driver)
- CORRECT result: +16.445
- Lesson: Always print TOP 5 results to verify the correct row before answering

### Case H: Returning all rows instead of filtered (task_25)
- Question: "Which event has the lowest cost?"
- WRONG: Returned all 13 events
- CORRECT: Only 3 events tied at the lowest cost
- Lesson: "Lowest" may have ties — check with GROUP BY and return ALL tied results
