# DABench ReAct Baseline

[English](README.md) | 中文

这个仓库包含 DABench 的公开 ReAct baseline。它会读取公开 demo 数据集任务，并生成 `prediction.csv` 供后续评测使用。

## 数据集

公开 demo 数据集默认位于 `data/public/input/`。每个任务目录结构如下：

```text
data/public/input/task_<id>/
├── task.json
└── context/
```

公开 demo 数据集的标准答案文件单独放在 `data/public/output/task_<id>/gold.csv`。hidden test set 只提供 `input/`，不会包含 `output/`。

`task.json` 包含：

- `task_id`
- `difficulty`
- `question`

`context/` 中可能包含一种或多种数据：

- CSV 文件
- JSON 文件
- SQLite / DB 文件
- 文本文档

## 安装

```bash
uv sync
```

## 配置

示例配置文件位于 `configs/react_baseline.example.yaml`。

```yaml
dataset:
  root_path: data/public/input

agent:
  model: YOUR_MODEL_NAME
  api_base: YOUR_API_BASE_URL
  api_key: YOUR_API_KEY
  max_steps: 16
  temperature: 0.0

run:
  output_dir: artifacts/runs
  run_id:
  max_workers: 4
  task_timeout_seconds: 900
```

配置字段说明：

- `dataset.root_path`
  - 公开 demo `input/` 数据集根目录
  - 相对路径按项目根目录解析
- `agent.model`
  - 模型名称
- `agent.api_base`
  - OpenAI-compatible 接口根地址
- `agent.api_key`
  - API key，直接从配置文件读取
- `agent.max_steps`
  - 单个任务允许的最大 ReAct 步数
- `agent.temperature`
  - 模型采样温度
- `run.output_dir`
  - 运行产物输出目录
- `run.run_id`
  - 可选，指定运行目录名
  - 不传时默认使用 UTC 时间戳
  - 必须是单个目录名；若目录已存在会直接报错
- `run.max_workers`
  - `run-benchmark` 并行 worker 数
- `run.task_timeout_seconds`
  - 单个任务允许的最长墙钟时间
  - 设为 `0` 或负数可关闭任务级超时

## CLI

CLI 入口：

```bash
uv run dabench <command> --config PATH [options]
```

### `status`

作用：

- 查看项目路径
- 查看当前实际使用的配置文件路径
- 查看当前实际使用的数据集根目录
- 查看公开任务数量统计

用法：

```bash
uv run dabench status --config configs/react_baseline.example.yaml
```

参数：

- `--config PATH`
  - YAML 配置文件路径
  - 必填

### `inspect-task`

作用：

- 查看单个任务元信息
- 列出 `context/` 下可访问文件

用法：

```bash
uv run dabench inspect-task task_1 --config configs/react_baseline.local.yaml
```

参数：

- `task_id`
  - 必填位置参数
- `--config PATH`
  - YAML 配置文件路径
  - 必填

### `run-task`

作用：

- 对单个任务运行 ReAct baseline
- 调用模型、执行工具并写出结果

用法：

```bash
uv run dabench run-task task_1 --config configs/react_baseline.local.yaml
```

参数：

- `task_id`
  - 必填位置参数
- `--config PATH`
  - YAML 配置文件路径
  - 必填

### `run-benchmark`

作用：

- 批量运行整个公开数据集
- 对于公开 demo 集，可以对照单独的 `data/public/output/` 中的标准答案
- 写出每个任务的结果和整次运行摘要
- 运行过程中显示紧凑型实时进度条，包含成功/失败统计和吞吐

用法：

```bash
uv run dabench run-benchmark --config configs/react_baseline.local.yaml
uv run dabench run-benchmark --config configs/react_baseline.local.yaml --limit 5
uv run dabench run-benchmark --config configs/react_baseline.local.yaml --limit 20
```

参数：

- `--config PATH`
  - YAML 配置文件路径
  - 必填
- `--limit N`
  - 最多运行多少个任务

## Tools

当前暴露给模型的工具有：

- `list_context`
  - 列出 `context/` 下的文件和目录
  - 输入：`max_depth`
- `read_csv`
  - 读取 CSV 预览
  - 输入：`path`、`max_rows`
- `read_json`
  - 读取 JSON 预览
  - 输入：`path`、`max_chars`
- `read_doc`
  - 读取文本文档预览
  - 输入：`path`、`max_chars`
- `inspect_sqlite_schema`
  - 查看 SQLite / DB 文件中的表结构
  - 输入：`path`
- `execute_context_sql`
  - 对 `context/` 内 SQLite / DB 文件执行只读 SQL
  - 输入：`path`、`sql`、`limit`
- `execute_python`
  - 在任务 `context/` 目录内执行任意 Python 代码
  - 输入：`code`
  - 固定超时：`30` 秒
  - 返回：标准输出 `output`
- `answer`
  - 提交最终答案表格并结束当前任务
  - 输入：`columns`、`rows`

所有文件路径都必须是相对于任务 `context/` 目录的相对路径。

## 输出

每个任务运行后可能生成：

- `trace.json`
- `prediction.csv`

单任务产物路径：

```text
artifacts/runs/<run_id>/<task_id>/
├── trace.json
└── prediction.csv
```

批量运行还会额外生成：

```text
artifacts/runs/<run_id>/summary.json
```

## 主要模块

- `src/data_agent_baseline/benchmark/dataset.py`
  - 公开数据集加载器
- `src/data_agent_baseline/tools/filesystem.py`
  - `list_context`、`read_csv`、`read_json`、`read_doc`
- `src/data_agent_baseline/tools/python_exec.py`
  - `execute_python`
- `src/data_agent_baseline/tools/sqlite.py`
  - `inspect_sqlite_schema`、`execute_context_sql`
- `src/data_agent_baseline/tools/registry.py`
  - 工具注册与终止型 `answer`
- `src/data_agent_baseline/agents/prompt.py`
  - system prompt、task prompt、observation prompt
- `src/data_agent_baseline/agents/react.py`
  - 基于 JSON action 协议的 ReAct runtime
- `src/data_agent_baseline/run/runner.py`
  - 单任务和批量运行逻辑
