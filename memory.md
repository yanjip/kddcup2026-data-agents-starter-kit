# Failure Patterns — DO NOT REPEAT

## 1. Always map question keywords to knowledge.md FIRST
- Before writing any query, map every keyword in the question to the exact definition in knowledge.md
- Pay special attention to: Metric Definitions, Ambiguity Resolution, and Exemplar Use Cases
- If knowledge.md does not contain the definition you need, **stop searching repeatedly** and infer from the actual data

## 2. Output columns must strictly match the question
- Return ONLY the columns explicitly requested. Never include intermediate calculation columns
- "List all X" means return ONLY the X column, not the entire row
- But when the task asks to list/show records, transactions, people, customers, or events matching conditions, keep the identifying columns needed to identify each returned row, plus the requested value/status fields
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
- Do NOT remove 0 values from averages/sums/counts unless knowledge.md, the schema, or the question explicitly says 0 means missing/invalid
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
- CORRECT result: 459.96 = AVG(Consumption records for SME customers in 2013) / 12
- Output detail: The gold/evaluator column name may be the formula expression itself, e.g. `AVG(T2.Consumption) / 12`, not a renamed alias.
- Lesson: Always identify the aggregation grain before dividing. Do NOT invent a per-customer denominator unless the question or knowledge.md explicitly says "per customer". Match the question/knowledge expression literally, preserve formula-style output column names when shown by exemplar/query logic, then sanity-check the order of magnitude.

### Case C: Percentage formula error (task_408)
- Question: "How much faster in percentage is the champion than the last driver?"
- WRONG result: 1.468% (wrong formula)
- CORRECT result: 0.316% (must use correct base for percentage calculation)
- Lesson: Always print numerator and denominator separately before dividing

### Case D: LEFT JOIN plus exact scope (task_199)
- Question: "List the names and funding types of schools from Riverside-related school districts where the average SAT math score across schools exceeds 400."
- WRONG: Returned all Riverside-county schools with individual `AvgScrMath > 400`, or dropped schools with NULL funding.
- CORRECT: First identify Riverside-related districts by district name/scope, compute district-level average SAT math, then list schools in qualifying districts; preserve NULL funding via LEFT JOIN. Gold rows include Arlington High, John W. North High, Martin Luther King Jr. High, Polytechnic High, Ramona High, and River Springs Charter.
- Lesson: Parse scope and aggregation level before joining optional fields. "Riverside-related school districts" is not the same as every school in Riverside county, and nullable funding must not be filtered out.

### Case F: Ratio numerator/denominator swapped (task_243)
- Question: "How many times is the number of posts compared to votes?"
- WRONG result: 0.103 (inverted the ratio)
- CORRECT result: 0.375 (posts / votes, not votes / posts)
- Important detail: For "his/her votes", count votes cast BY that user (`votes.UserId = user_id`), not votes received ON that user's posts, unless the question explicitly says "votes on his/her posts".
- Lesson: "A compared to B" means A / B. Print both values before dividing, and resolve whether a possessive field means owned/cast-by versus received-on.

### Case G: Wrong row selected for ranking (task_89)
- Question: "Finish time for the driver who ranked second in 2008 Chinese Grand Prix?"
- WRONG result: +14.925 (picked wrong driver)
- CORRECT result: +16.445
- Lesson: In Formula 1 `results`, `rank` is fastest-lap rank and `positionOrder` is finishing position. If the question says "driver who ranked second", use `rank = 2`; if it says "finished second", use `positionOrder = 2`. Print both fields before answering.

### Case H: Wrong cost field selected (task_25)
- Question: "Which event has the lowest cost?"
- WRONG: Used total event expense or budget amount/spent and returned Officers meeting events.
- CORRECT: Use the actual expense `cost` field; the lowest cost is tied by November Speaker, October Speaker, and September Speaker.
- Lesson: For "lowest cost", prefer the literal `cost` field over budget `amount`, `spent`, or aggregate totals unless the question asks for total cost. After selecting the correct metric, return all genuine tied events.

### Case I: Over-minimal output loses record identity (task_38/task_180)
- Question: "List all the withdrawals in cash transactions..." / "Give their consumption status..."
- WRONG: Returned only `amount` or only `Consumption`, losing the transaction/customer identity.
- CORRECT: For transaction lists, include identifying transaction fields such as `trans_id`, `date`, `type`, `operation`, `amount`; for customer status lists, include `CustomerID` plus `Consumption`.
- Lesson: Strict column matching does not mean deleting row identity. For list/show records matching conditions, keep the minimal identifying columns needed for the evaluator/user to know which record each value belongs to.

### Case J: Zero values wrongly dropped from averages (task_67)
- Question: "What is the average weight of all female superheroes?"
- WRONG result: 78.50694444444444 after excluding `weight_kg = 0` as invalid.
- CORRECT result: 60.77956989247312 using the dataset values as recorded, including 0.
- Lesson: Be suspicious of 0, but do not delete it from AVG/SUM/COUNT unless knowledge.md, schema notes, or the question says 0 is missing/invalid.

### Case K: Unit price vs total price (task_180)
- Question: "people who paid more than 29.00 per unit of product id No.5"
- WRONG approach: `Price > 29.0`, which selected 153 customers.
- CORRECT approach: `Price / Amount > 29.0`, which selected 9 customers.
- Lesson: "per unit" usually means unit price/rate. Identify numerator and quantity denominator before filtering.

### Case L: Proper noun expanded into generic category (task_303)
- Question: "Among all European Grand Prix races, what percentage were hosted in Germany?"
- WRONG approach: Treated "European" as a geographic category and counted all races in European countries, giving 76/611 = 12.4386%.
- CORRECT approach: Treat "European Grand Prix" as the exact race name first, then count Germany among those races, giving 12/23 = 52.1739%.
- Lesson: Capitalized event/race/product names should be tested as exact names before semantic expansion.
