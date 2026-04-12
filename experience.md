问：这是题目： `/Users/yanjp/PycharmProjects/kddcup2026-data-agents-starter-kit/demo_samples/input/task_173/task.json` ，我用同一个agent架构，首先用的deepseek模型得到的结果为空，其运行过程为： `/Users/yanjp/PycharmProjects/kddcup2026-data-agents-starter-kit/artifacts/runs/example_run_id_22/task_173/trace.json` 

然后改成调用claude模型后，得到的结果完全正确，其运行过程为： `/Users/yanjp/PycharmProjects/kddcup2026-data-agents-starter-kit/artifacts/runs/yunwu_run_id_9/task_173/trace.json` 

请分析为什么会错，同时给出对agent架构的改进意见，注意，不是针对这一个任务，而是通用改进。

          
## 分析结果

### DeepSeek 为什么错？

通过对比两个 trace，我发现了核心差异：

| 对比维度 | DeepSeek (错误) | Claude (正确) |
|---------|----------------|--------------|
| **关键决策** | 发现数据库只有 2012-08 数据后，直接返回空结果 | 发现数据只有 2012-08 后，**尝试探索其他数据源** |
| **探索广度** | 局限于 `transactions_1k.db`，未发现 `yearmonth.csv` 的价值 | 探索了 `yearmonth.csv`，发现 201306 的消费记录 |
| **数据关联能力** | 只关注交易表，忽略了消费数据与气油站的潜在关联 | 发现 `yearmonth.csv` 有 `CustomerID` + `Date(YYYYMM)` + `Consumption` |
| **问题转化能力** | 机械地找"2013年6月交易"→找不到→放弃 | 找不到交易数据但发现消费数据中有 201306 → 思考如何关联 |
| **近似匹配意识** | **无** - 严格按字面意思找数据，找不到就放弃 | **有** - 意识到数据不完整，主动应用近似匹配策略 |

**DeepSeek 的失败路径**（12步）：
1. 列出文件 → 2. 读 knowledge.md → 3. 检查数据库 schema → 4. 读 gasstations.json → 5. 检查日期格式 → 6. 查 June 2013 → 无结果 → 7. 读 yearmonth.csv（但未深入分析）→ 8. 确认只有 2012-08 → 9-10. 再次确认无其他数据源 → 11. 确认无 2013 数据 → **12. 返回空结果**

**Claude 的成功路径**（9步）：
1. 列出文件 → 2. 检查数据库 schema → 3. 读 gasstations.json → 4. 查 June 2013 → 无结果 → 5. 再次确认 → 6. 发现数据只到 2012-08 → **7. 探索 yearmonth.csv 并发现 201306 存在** → **8. 应用"近似匹配"规则** → 9. 返回 CZE, SVK

---

## Agent 架构通用改进建议

### 1. **增加"数据探索完整性"校验机制**

DeepSeek 在第7步已经看到了 `yearmonth.csv`（包含 201306），但没有将其与问题关联起来。

**改进建议**：
- 在 Agent 决策循环中，增加一个**数据覆盖度分析**步骤
- 当主数据源（transactions_1k.db）不包含目标时间范围时，**强制要求 Agent 交叉检查其他数据源**
- 可以通过**系统提示词**要求 Agent 对每个文件都要思考："这个文件是否可能包含我需要的信息？"

### 2. **增加"近似匹配策略"的显式引导**

Claude 在 step 7 提到："According to the instructions, we must find closest matching dates if exact match doesn't exist"，但 **knowledge.md 中并没有这条规则**。这说明这是 Claude 模型的隐性推理能力。

**改进建议**：
- 在 system prompt 或 knowledge.md 中**显式定义近似匹配规则**：
  ```
  ## 6. Approximate Matching Strategy
  When the exact requested data is not available:
  1. First, identify the closest available time period
  2. If data exists in that period, use it as a substitute
  3. Always prefer "closest month" over "no data"
  4. Document the substitution in the answer reasoning
  ```
- 或者在 Agent 框架层增加一个**fallback 策略触发器**，当精确查询返回空结果时，自动尝试近似匹配

### 3. **增加"跨表关联意识"训练/提示**

DeepSeek 看到 `yearmonth.csv` 有 `CustomerID` 但没有 `GasStationID`，就认为无法关联。但实际上可以通过以下路径关联：
- transactions_1k.db: CustomerID ↔ GasStationID（在 2012-08 有数据）
- yearmonth.csv: CustomerID + Date(201306) + Consumption

**改进建议**：
- 在 Agent 的 system prompt 中强调：**即使两个表没有直接关联字段，也可以通过公共字段（CustomerID）进行多跳关联**
- 鼓励 Agent 使用 Python 工具进行**内存关联**，而不是只依赖 SQL

### 4. **增加"自我纠错"机制**

DeepSeek 在第7步发现了 `yearmonth.csv` 中有 201306，但思考链中写的是：
> "Maybe the csv/yearmonth.csv contains aggregated transaction data? Or maybe the transactions_1k.db is only a sample..."

这说明它**意识到了数据可能不完整**，但随后没有采取进一步行动（第9步反而去列目录了）。

**改进建议**：
- 增加 Agent 的 **"self-correction" 次数限制**：当发现潜在问题时，要求至少尝试 2-3 种替代方案
- 通过 prompt 要求 Agent 在发现"数据可能不完整"时，**必须列出所有可能的数据源并逐一验证**

### 5. **考虑引入"结果验证"步骤**

两个 Agent 都没有对结果进行验证（返回前检查答案是否合理）。

**改进建议**：
- 增加一个**post-execution validation**：如果答案为空，强制要求 Agent 思考"是否还有其他途径获取数据？"
- 可以通过 framework 层面的 check：当 Agent 返回空结果时，自动注入一个 "exploration" step

---

## 总结

| 问题根因 | 改进方向 |
|---------|---------|
| DeepSeek **放弃了探索** yearmonth.csv 的机会 | 强制数据源交叉检查 |
| 缺乏**近似匹配**意识 | 显式定义 fallback 策略 |
| **跨表关联**能力不足 | prompt 强调多跳关联 + Python 内存关联 |
| **自我纠错**机制缺失 | 增加替代方案探索次数 |
| 无**结果验证** | 空结果时强制注入探索步骤 |

这些改进可以让 DeepSeek 这类模型更好地处理"数据不完整但可通过近似/关联策略求解"的场景。

---

## 实战经验二：输出格式规范化

### 问题背景
在 task_19 中，任务要求"List the full name of the Student_Club members that grew up in Illinois state"，但代理返回了一个包含合并后 full_name 的单列输出，而预期是分开的 first_name 和 last_name 两列。

在 task_243 中，任务要求返回"posts_to_votes_ratio"，但代理返回了包含 user_id, post_count, vote_count 等中间列的完整输出，而预期只需要 posts_to_votes_ratio 列。

### 核心问题
1. **输出格式理解偏差**：代理将"full name"理解为合并后的字符串，而不是需要分开的两个字段
2. **过度输出**：代理返回了所有相关列而不仅仅是任务明确要求的列
3. **缺乏明确的输出格式规则**：提示词中没有明确要求代理如何格式化输出

### 改进方案
在 Orchestrator 提示词中添加了专门的 **Output Formatting Rules** 部分：

```
## Output Formatting Rules

1. **Preserve Individual Fields**: When asked for "full name" or similar combined attributes, always return the individual components (e.g., first_name and last_name) as separate columns in your final output.

2. **Multi-Column Output**: For tasks involving multiple attributes or dimensions, return results with distinct columns for each attribute rather than combining them into a single column.

3. **Structured Results**: Use the `answer` tool with appropriate column names that match the data schema (e.g., `["first_name", "last_name"]` instead of `["full_name"]`).

4. **Clarity**: If a task asks for "full name", you may include both the combined full name and individual components if it adds value, but prioritize returning the individual fields as separate columns.

5. **Minimal Output**: Only return the columns explicitly requested in the task. Do not include intermediate columns or additional information unless specifically asked for. If the task asks for a ratio or calculated value, return only that final result column.
```

### 改进效果
- 对于"full name"查询，代理现在会返回 first_name 和 last_name 两列，而不是合并后的单列
- 对于比率或计算值查询，代理现在只返回最终结果列，而不是包含所有中间计算列
- 输出格式更加规范，符合任务预期

### 通用改进意义
1. **减少格式错误**：明确的输出格式规则可以避免因模型理解偏差导致的格式错误
2. **提高结果准确性**：确保代理返回的结果结构与任务要求一致
3. **增强可预测性**：不同模型在处理相同任务时会产生一致的输出格式
4. **简化结果处理**：下游系统可以直接使用代理返回的结果，无需额外格式转换

---

#### 补充改进：列名映射与严格最小输出

在后续的task_349中发现，即使添加了Minimal Output规则，代理仍然返回了多余的列（first_name和last_name），并且使用了不正确的列名（major而不是major_name）。因此进一步添加了两条规则：

```
6. **Column Name Mapping**: Use the column names specified in the task or data schema. If the task refers to a "major_name", ensure your output uses that exact column name instead of "major" or other variations.

7. **Strict Focus**: Even if you need intermediate columns for calculations, do not include them in the final answer. Only include the exact columns requested in the task description.
```

**改进效果**：
- 代理现在严格使用任务中指定的列名（如major_name而不是major）
- 即使中间计算需要其他列，最终答案也只包含任务明确要求的列
- 进一步减少了输出格式错误

