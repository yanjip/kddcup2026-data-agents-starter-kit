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