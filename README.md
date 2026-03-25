# DABench ReAct Baseline

English | [中文](README.zh.md)

This repository contains the public ReAct baseline for DABench. It reads tasks from the released demo dataset and produces `prediction.csv` files for downstream evaluation.

## Dataset

The public demo dataset is expected at `data/public/input/`. Each task directory follows this structure:

```text
data/public/input/task_<id>/
├── task.json
└── context/
```

Ground-truth files for the public demo dataset are stored separately under `data/public/output/task_<id>/gold.csv`. Hidden test sets only provide `input/` and do not include `output/`.

`task.json` contains:

- `task_id`
- `difficulty`
- `question`

The `context/` directory may contain one or more of:

- CSV files
- JSON files
- SQLite / DB files
- Text documents

## Install

```bash
uv sync
```

## Configuration

An example config file lives at `configs/react_baseline.example.yaml`.

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

Config fields:

- `dataset.root_path`
  - Root directory of the public demo `input/` dataset
  - Relative paths are resolved from the project root
- `agent.model`
  - Model name
- `agent.api_base`
  - OpenAI-compatible API base URL
- `agent.api_key`
  - API key, read directly from the config file
- `agent.max_steps`
  - Maximum ReAct steps per task
- `agent.temperature`
  - Sampling temperature
- `run.output_dir`
  - Output directory for run artifacts
- `run.run_id`
  - Optional run directory name
  - Defaults to a UTC timestamp if omitted
  - Must be a single directory name; existing run directories are rejected
- `run.max_workers`
  - Parallel worker count for `run-benchmark`
- `run.task_timeout_seconds`
  - Maximum wall-clock time per task
  - Set to `0` or a negative value to disable the task-level timeout

## CLI

The CLI entrypoint is:

```bash
uv run dabench <command> --config PATH [options]
```

### `status`

Purpose:

- Show project paths
- Show the active config path
- Show the active dataset root
- Show public task counts

Usage:

```bash
uv run dabench status --config configs/react_baseline.example.yaml
```

Parameters:

- `--config PATH`
  - YAML config file path
  - Required

### `inspect-task`

Purpose:

- Show a task's metadata
- List accessible files under `context/`

Usage:

```bash
uv run dabench inspect-task task_1 --config configs/react_baseline.local.yaml
```

Parameters:

- `task_id`
  - Required positional argument
- `--config PATH`
  - YAML config file path
  - Required

### `run-task`

Purpose:

- Run the ReAct baseline on one task
- Execute tools and write outputs

Usage:

```bash
uv run dabench run-task task_1 --config configs/react_baseline.local.yaml
```

Parameters:

- `task_id`
  - Required positional argument
- `--config PATH`
  - YAML config file path
  - Required

### `run-benchmark`

Purpose:

- Run the baseline across the public dataset
- For the public demo set, compare against the separate `data/public/output/` ground truth
- Write per-task outputs plus a run summary
- Show a compact live progress bar with success/failure counts and throughput

Usage:

```bash
uv run dabench run-benchmark --config configs/react_baseline.local.yaml
uv run dabench run-benchmark --config configs/react_baseline.local.yaml --limit 5
uv run dabench run-benchmark --config configs/react_baseline.local.yaml --limit 20
```

Parameters:

- `--config PATH`
  - YAML config file path
  - Required
- `--limit N`
  - Maximum number of tasks to run

## Tools

The baseline exposes these tools to the model:

- `list_context`
  - List files and directories under `context/`
  - Inputs: `max_depth`
- `read_csv`
  - Read a CSV preview
  - Inputs: `path`, `max_rows`
- `read_json`
  - Read a JSON preview
  - Inputs: `path`, `max_chars`
- `read_doc`
  - Read a text document preview
  - Inputs: `path`, `max_chars`
- `inspect_sqlite_schema`
  - Inspect tables in a SQLite / DB file
  - Inputs: `path`
- `execute_context_sql`
  - Execute read-only SQL against a SQLite / DB file in `context/`
  - Inputs: `path`, `sql`, `limit`
- `execute_python`
  - Execute arbitrary Python code inside the task `context/` directory
  - Inputs: `code`
  - Fixed timeout: `30` seconds
  - Returns captured stdout as `output`
- `answer`
  - Submit the final answer table and terminate the task
  - Inputs: `columns`, `rows`

All file paths passed to tools must be relative to the task `context/` directory.

## Outputs

Each successful task run may produce:

- `trace.json`
- `prediction.csv`

Per-task outputs are written to:

```text
artifacts/runs/<run_id>/<task_id>/
├── trace.json
└── prediction.csv
```

Benchmark runs also write:

```text
artifacts/runs/<run_id>/summary.json
```

## Main Modules

- `src/data_agent_baseline/benchmark/dataset.py`
  - Public dataset loader
- `src/data_agent_baseline/tools/filesystem.py`
  - `list_context`, `read_csv`, `read_json`, `read_doc`
- `src/data_agent_baseline/tools/python_exec.py`
  - `execute_python`
- `src/data_agent_baseline/tools/sqlite.py`
  - `inspect_sqlite_schema`, `execute_context_sql`
- `src/data_agent_baseline/tools/registry.py`
  - Tool registration and terminal `answer`
- `src/data_agent_baseline/agents/prompt.py`
  - System prompt, task prompt, observation prompt
- `src/data_agent_baseline/agents/react.py`
  - ReAct runtime with JSON action protocol
- `src/data_agent_baseline/run/runner.py`
  - Single-task and benchmark execution
