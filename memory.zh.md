# DataAgent 失败经验记录

> 本文件记录各任务失败根因与应对策略，供后续跑分前复盘使用。

---

## 一、理解错误类（一致性投票无法捕获）

### 1. task_25 — "Which event has the lowest cost?"

**失败表现**：返回 12 个 cost=0 的事件，Gold 答案只有 1 个。

**根因**：
- 模型把"没有支出记录的活动"（status=Planning，cost=0）当成了"成本最低"
- 双方法投票一致确认了 12 个 0 元事件，但投票只能抓计算错误，抓不了理解错误

**应对策略**：
- 遇到 MIN/MAX/LOWEST/HIGHEST 问题，若最小值是 0 或 NULL，必须验证是否为真实测量值
- 检查 status 字段，排除 Planning/Open 等未发生状态
- 题目用单数"Which event"时，预期答案应为 1 行，返回多行必须反思

---

### 2. task_38 — "For client id 3356, how many cash withdrawals..."

**失败表现**：返回 7 列空表，Gold 只有 1 列。

**根因**：
- 模型返回了交易表的完整列（trans_id, account_id, date, type, operation, amount, balance）
- 题目问的是"how many"，应该返回 1 个计数，而不是完整表结构

**应对策略**：
- "How many..." → 只返回 1 列（计数）
- "Which X..." → 只返回与 X 相关的列
- 返回空表时也要检查列数，不要带无关字段

---

### 3. task_180 — "For all people who paid >29.00 per unit of product id No.5..."

**失败表现**：返回 CustomerID + Consumption 两列，Gold 只有 1 列。

**根因**：
- 题目问"give their consumption status"，Gold 只需要 Consumption 一列
- 模型自作主张加了 CustomerID 作为标识列

**应对策略**：
- "Give me their Y..." → 只返回 Y，不要带标识列（除非题目明确要求）
- 严格按题目要求控制列数

---

## 二、超时/步骤耗尽类

### 4. task_418 — "Among patients whose creatinine level is abnormal, how many aren't 70 yet?"

**失败表现**：33 步耗尽，未提交答案。

**根因**：
- Patient.md 是 85KB 非结构化文本，模型用正则按行解析
- 性别和生日分散在不同行，按行处理导致永远关联不上
- 花了 30 步调正则表达式，没时间做最终计算

**应对策略**：
- 大文档（>50KB）必须用分块并行 SubAgent 处理
- 不要按行解析连续文本，应该按段落/记录块切分
- 正则表达式调试不要超过 3 步，不行就换策略

---

### 5. task_420 — "What percentage of cards with format commander..."

**失败表现**：31 步耗尽，未提交答案。

**根因**：
- SubAgent 先崩了（answer.columns must be non-empty）
- 主Agent从头再来，时间不够

**应对策略**：
- SubAgent 失败后，主Agent应直接接手而非再 fork
- 简单任务不要用 SubAgent，自己直接做

---

## 三、SubAgent 工具错误类

### 6. SubAgent 频繁报错

**错误类型**：
- `Unknown tool: read_file`
- `Unknown tool: sqlite_query`
- `answer.columns must be non-empty`

**根因**：
- SubAgent 的 system prompt 引用了不存在的工具名
- SubAgent 被赋予任务后，调用 answer 时列名为空

**应对策略**：
- SubAgent 只读 knowledge.md 时，明确告诉它用 `read_doc` 而非 `read_file`
- SubAgent 任务描述要明确预期输出格式
- 若 SubAgent 连续失败 2 次，主Agent直接接手

---

## 四、数据类型陷阱类

### 7. task_180 — Date 字段类型匹配

**失败表现**：第一次查询 0 条结果，因为用字符串 '201208' 匹配整数 201208。

**根因**：
- CSV 里的 Date 列是整数类型（201208），模型用字符串 '201208' 过滤

**应对策略**：
- 过滤前先用 `execute_python` 检查列的 dtype
- 特别是日期/时间字段，可能为 int、str、datetime 任意类型

---

## 五、空结果排查类

### 8. task_38 — 空结果但列数超标

**失败表现**：Client 3356 没有 VYBER 交易，返回空表但带了 7 列。

**根因**：
- 空结果时模型没有压缩列数，仍然返回了完整表结构

**应对策略**：
- 空结果也要检查列数，只保留题目要求的列
- 空结果时反思：是真的没有数据，还是过滤条件太严格？

---

## 六、通用检查清单（跑分前必读）

### 提交答案前强制检查：

1. **行数**：是否超过题目暗示的数量？单数问题（Which/What is）应只有 1 行
2. **列数**："How many"→1列，"Which X"→X相关列，不要带无关字段
3. **零值/NULL**：MIN/MAX 问题中，0 或 NULL 是否为真实值？
4. **状态过滤**：是否排除了 Planning、未发生、未激活的记录？
5. **数据类型**：字符串和整数是否混用？日期格式是否统一？
6. **空结果**：0 行时检查过滤条件是否太严格、数据类型是否匹配
