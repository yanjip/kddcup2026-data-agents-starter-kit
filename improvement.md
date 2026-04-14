
我已经完成了所有的修改，具体包括：

1. 改进系统提示词 ：
   
   - 在 prompt.py 中修改了 REACT_SYSTEM_PROMPT ，强调模型需要先分析任务并创建清晰的计划，然后再执行操作
   - 增加了规则，要求模型高效地使用工具，只关注计划中必要的内容
   - 提醒模型避免不必要的步骤和循环，通过遵循初始计划来提高效率
2. 实现自动预加载 Schema 知识 ：
   
   - 添加了 SchemaKnowledge 数据类，用于存储从 knowledge.md 解析出的表结构和语义映射
   - 实现了 _auto_load_schema_knowledge 方法，在运行开始时自动：
     - 调用 list_context 查找 knowledge.md 路径
     - 直接读取并解析内容
     - 填充 SchemaKnowledge 对象（表、列、语义映射）
   - 修改了 _build_messages 方法，将 schema knowledge 集成到提示词中，让模型能够直接利用这些知识
   - 修改了 run 方法，在调用 _build_messages 时传入 schema_knowledge 参数
3. 验证代码正确性 ：
   
   - 运行了诊断工具，检查代码是否有任何错误，结果显示没有错误
这些修改应该能够解决您提出的问题：

- 通过改进提示词，让模型先规划再执行，减少不必要的循环
- 通过自动预加载 Schema 知识，节省 2-3 步，因为模型不需要手动读取 knowledge.md
现在，当模型运行时，它会：

1. 首先分析任务并创建一个清晰的计划
2. 自动加载 knowledge.md 中的 schema 知识
3. 根据计划和已有的 schema 知识高效地执行操作
4. 避免不必要的步骤和循环
这样，即使设置了最大 15 步循环，简单的任务也应该能够在限制次数内完成。

## 优化内容
### 1. 增强的系统提示词 (react.py)
- 新增 F1时间格式处理 说明：明确指出"0:01:54"应理解为"1:54"格式
- 新增 近似匹配 指导：当没有精确匹配时，应该找最接近的值
- 强调 当找到可能的答案时立即提交 （即使答案是近似的）
### 2. 停滞检测机制 (react.py)
- _detect_stalling() 方法检测重复的Python执行模式
- 当连续4步执行相同的action时，自动发出提示让agent换策略
### 3. 反思提示机制 (react.py)
- _build_reflection_prompt() 在剩余步骤≤3时触发
- 提供关键问题引导agent回顾已收集的数据
- 提醒agent检查是否有足够信息回答问题
### 4. 更新 _build_messages 方法 (react.py)
- 新增 reflection_hint 参数
- 当检测到停滞或接近max_steps时，自动注入反思指导


## 架构说明
### 1. 新增文件
- subagent.py - 包含新的subagent架构实现
### 2. 核心组件
OrchestratorAgent（主编排器） ：

- 负责任务分析和整体规划
- 可以fork最多3个子agent来处理子任务
- 通过 fork_subagent action来派生子agent
- 继承完整的system prompt和工具集
SubAgent（子agent） ：

- 以ReAct方式执行子任务
- 继承父agent的完整上下文：
  - System prompt（包含工具描述）
  - 完整对话历史
  - 工具注册表
- 独立执行任务并返回结果
### 3. 使用方式
```
# 使用原有的 ReAct agent（默认）
dabench run-task --task-id xxx --config 
config.yaml

# 使用新的 Orchestrator agent（支持fork 
subagent）
dabench run-task --task-id xxx --config 
config.yaml --agent-type orchestrator

# benchmark也同样支持
dabench run-benchmark --config config.
yaml --agent-type orchestrator
```
### 4. Fork机制
当Orchestrator识别到适合并行执行的子任务时，返回：

```
{"thought":"...", 
"action":"fork_subagent", "action_input":
{"task_description":"...", 
"task_context":"...", 
"expected_output":"..."}}
```
子agent会继承父agent的完整上下文，独立执行任务并将结果返回给主agent进行合成。


根据代码分析，这两个文件的关系如下：

## 引用关系

**subagent.py 引用了 react.py 的内容：**

```python
# subagent.py 第 6-9 行
from data_agent_baseline.agents.prompt import (
    REACT_SYSTEM_PROMPT,          # ← 来自 react.py 定义的常量
    build_observation_prompt,
    build_system_prompt,
    build_task_prompt,
)

from data_agent_baseline.agents.runtime import AgentRuntimeState, StepRecord  # ← 共享的数据类
```

**react.py 不引用 subagent.py 的任何内容**

---

## 两者关系

| 文件 | 角色 | 说明 |
|------|------|------|
| [react.py](file:///Users/yanjp/PycharmProjects/kddcup2026-data-agents-starter-kit/src/data_agent_baseline/agents/react.py) | 基础代理 | 定义 `ReActAgent`，单一代理模式，独立完成任务 |
| [subagent.py](file:///Users/yanjp/PycharmProjects/kddcup2026-data-agents-starter-kit/src/data_agent_baseline/agents/subagent.py) | 扩展代理 | 定义 `OrchestratorAgent`（编排器）和 `SubAgent`（子代理） |

## 层次结构

```
ReActAgent (react.py)
    └── 独立完成任务的单一代理
         - 循环调用 model.complete() + tools.execute()
         - max_steps 次迭代

OrchestratorAgent (subagent.py)  ← 高级代理
    ├── 管理工作流程，决定何时 fork
    ├── fork_subagent action → 创建 SubAgent
    │
    └── SubAgent (subagent.py)  ← 使用 REACT_SYSTEM_PROMPT
        ├── 继承 OrchestratorAgent 的上下文
        └── 也是 ReAct 风格的代理
```

**关键点**：`OrchestratorAgent` 和 `SubAgent` 都使用了 `REACT_SYSTEM_PROMPT`（定义在 react.py），但它们的架构不同：
- `ReActAgent`：单一代理，自我循环
- `OrchestratorAgent`：编排器，可以 fork 多个 `SubAgent` 并行执行子任务


## JSON 解析问题修复记录

### 问题现象

在运行多个 task 时，Agent 反复出现 JSON 解析失败，导致任务无法完成。主要错误包括：

1. **括号不匹配错误**：`Expecting ',' delimiter`
   - LLM 生成的 `answer` 工具调用中，`rows` 数组缺少闭合方括号
   - 例如：`"rows":[["Sacha","Harrison",866.25]}}`（少了一个 `]`）

2. **未转义的换行符**：`Invalid control character`
   - LLM 在 `thought` 字段中使用了实际的换行符（`\n`），而非转义后的 `\\n`

3. **多余字符错误**：`Model response must contain only one JSON object`
   - LLM 在 JSON 对象后添加了多余的双引号或其他字符
   - 例如：`{"action":"list_context"...}}"}`（末尾多了 `"`）

### 根因分析

1. **LLM 输出不稳定**：即使 prompt 中明确要求正确的 JSON 格式，LLM 仍可能生成格式错误的 JSON
2. **缺乏容错机制**：原始的 `parser.py` 对 JSON 格式要求严格，任何微小错误都会导致解析失败
3. **SubAgent 缺少系统提示词**：SubAgent 继承 Orchestrator 的消息，但没有自己的系统提示词来指导 JSON 格式

### 修复方案

#### 1. 增强 parser.py 的容错能力

在 `src/data_agent_baseline/agents/parser.py` 中添加三层修复机制：

**a) `_sanitize_json_string` 函数**：清理字符串中的非法控制字符
- 将 JSON 字符串值中的实际换行符（`\n`）、回车符（`\r`）、制表符（`\t`）转换为转义形式
- 处理其他控制字符（ASCII < 32）为 Unicode 转义序列

**b) `_fix_common_json_errors` 函数**：修复常见的括号不匹配问题
- 检测方括号 `[`/`]` 和花括号 `{`/`}` 的数量不匹配
- 当在数组上下文中遇到多余的 `}` 时，将其替换为 `]`
- 补充或移除多余的闭合括号

**c) `_strip_trailing_garbage` 函数**：移除 JSON 后的多余字符
- 通过跟踪花括号深度，找到第一个完整的 JSON 对象
- 丢弃 JSON 对象后的任何多余字符（如多余的双引号）

#### 2. 为 SubAgent 添加专用系统提示词

在 `src/data_agent_baseline/agents/prompt.py` 中：

- 新增 `SUBAGENT_RESPONSE_EXAMPLES`：提供 SubAgent 专用的响应示例
- 新增 `build_subagent_system_prompt()` 函数：构建包含明确 JSON 格式指导的系统提示词
- 特别强调 `answer` 工具的正确格式和括号匹配规则

在 `src/data_agent_baseline/agents/subagent.py` 中：
- 修改 `_build_messages_with_schema` 方法，在第一步时添加系统提示词

#### 3. 添加模型请求重试机制

在 `src/data_agent_baseline/agents/model.py` 中：
- 为 `OpenAIModelAdapter.complete` 方法添加最多 5 次的重试机制
- 使用指数退避策略（0.5s, 1s, 1.5s, 2s, 2.5s）避免频繁请求
- 捕获 `APIError` 和其他异常，记录日志后自动重试

### 修复效果

经过上述修复后：
- JSON 解析成功率显著提高，即使 LLM 生成格式错误的 JSON，也能自动修复
- SubAgent 有了明确的格式指导，生成错误 JSON 的概率降低
- 模型请求失败时自动重试，提高了整体稳定性
- 多个 task（如 task_27、task_80 等）从反复报错变为能够正常执行

### 相关文件

- `src/data_agent_baseline/agents/parser.py` - JSON 解析和修复逻辑
- `src/data_agent_baseline/agents/prompt.py` - SubAgent 系统提示词
- `src/data_agent_baseline/agents/subagent.py` - SubAgent 消息构建
- `src/data_agent_baseline/agents/model.py` - 模型请求重试机制

