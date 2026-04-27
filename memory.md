# Failure Patterns — DO NOT REPEAT

## Output Shape
- "How many" → 1 column (count only), never full table
- "Which X" / "Give their Y" → ONLY requested columns, no foreign keys
- Empty result → still use minimal correct columns, not full schema

## MIN / MAX / LOWEST / HIGHEST Traps
- Min=0 or NULL → verify it is a REAL value, not a placeholder
- Exclude status="Planning" / unoccurred / inactive records from comparisons
- Singular questions ("Which event") → expect 1 row; if many tie, re-check filters

## Data Types
- Check dtype before filtering: string '201208' != integer 201208
- Date columns may be int / str / datetime — verify before comparing

## SubAgent Limits
- SubAgent tools: read_doc, inspect_sqlite_schema, execute_python — NO read_file or sqlite_query
- If SubAgent fails twice, solve directly without re-forking

## Empty Result Checklist
- Data type mismatch? Case sensitivity? Overly strict filter? Wrong value in condition?
