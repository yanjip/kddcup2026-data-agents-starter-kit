# Docker 打包与提交操作指南

> 本文档记录如何将项目打包为 Docker 镜像并提交给 KDD Cup 2026 主办方。  
> Team ID: `team1194`（请根据实际情况替换）

---

## 1. 前置条件

确保 Docker Desktop 已启动：

```bash
open -a Docker
```

验证 Docker 可用：

```bash
docker info
```

---

## 2. 构建镜像

在项目根目录执行：

```bash
cd /Users/caramel/kddcup2026-data-agents-starter-kit

docker build --platform linux/amd64 -t team1194:v1 .
```

> 将 `team1194` 替换为你的 Team ID，`v1` 替换为版本号（v1, v2, v3...）

构建成功后，查看镜像：

```bash
docker images team1194:v1
```

---

## 3. 本地测试（模拟评测环境）

### 3.1 测试数据读取

```bash
mkdir -p /tmp/team1194_output /tmp/team1194_logs

docker run --rm \
  -v $(pwd)/public/input:/input:ro \
  -v /tmp/team1194_output:/output:rw \
  -v /tmp/team1194_logs:/logs:rw \
  -e MODEL_API_URL=https://dashscope.aliyuncs.com/compatible-mode/v1 \
  -e MODEL_API_KEY=sk-c940fe1e2114483eb5bb753a18e5814d \
  -e MODEL_NAME=qwen3.5-35b-a3b \
  team1194:v1 \
  status --config configs/submission.yaml
```

期望输出：`dataset_root: /input` 状态为 `present`

### 3.2 测试运行一个任务

```bash
rm -rf /tmp/team1194_output /tmp/team1194_logs
mkdir -p /tmp/team1194_output /tmp/team1194_logs

docker run --rm \
  -v $(pwd)/public/input:/input:ro \
  -v /tmp/team1194_output:/output:rw \
  -v /tmp/team1194_logs:/logs:rw \
  -e MODEL_API_URL=https://dashscope.aliyuncs.com/compatible-mode/v1 \
  -e MODEL_API_KEY=sk-c940fe1e2114483eb5bb753a18e5814d \
  -e MODEL_NAME=qwen3.5-35b-a3b \
  team1194:v1 \
  run-benchmark --config configs/submission.yaml --limit 1
```

### 3.3 验证输出结构

```bash
echo "=== 输出目录 ==="
find /tmp/team1194_output -type f | sort

echo "=== summary.json ==="
cat /tmp/team1194_output/summary.json

echo "=== 日志文件 ==="
ls -la /tmp/team1194_logs/
```

期望看到：
- `/tmp/team1194_output/summary.json`
- `/tmp/team1194_output/task_xxx/trace.json`
- `/tmp/team1194_logs/runtime.log`

---

## 4. 导出镜像

```bash
cd /Users/caramel/kddcup2026-data-agents-starter-kit

docker save team1194:v1 | gzip > team1194_v1.tar.gz
```

验证文件：

```bash
ls -lh team1194_v1.tar.gz
```

要求：文件大小 **≤ 10 GB**

---

## 5. 提交给主办方

### 5.1 上传到 Google Drive

1. 打开 [Google Drive](https://drive.google.com)
2. 上传 `team1194_v1.tar.gz`
3. 右键文件 → **分享** → **知道链接的任何人** → 权限设为 **查看者**
4. 复制分享链接

### 5.2 发送邮件

- **收件人**：`kddcup@hkust-gz.edu.cn`
- **主题**：
  ```
  [KDDCup2026 Data Agents] Submission - team1194 - v1
  ```
- **正文**：
  ```text
  Team ID: team1194
  Version: v1
  Sharing link: https://drive.google.com/file/d/XXXXXX/view?usp=share_link
  ```

### 5.3 提交后注意事项

- 不要删除 Google Drive 上的文件，直到收到评测完成通知
- 确保分享链接在整个评测期间有效
- 每次提交版本号必须递增（v1 → v2 → v3），不能重复

---

## 6. 相关文件说明

| 文件 | 作用 |
|---|---|
| `Dockerfile` | Docker 镜像构建配置 |
| `configs/submission.yaml` | 提交专用配置（输入 `/input`，输出 `/output`） |
| `.dockerignore` | 排除不需要打包的文件，减小镜像体积 |
| `team1194_v1.tar.gz` | 最终提交的压缩包 |

---

## 7. 常见问题

### Q: 构建时 Docker daemon 未运行

```bash
open -a Docker
sleep 15
docker info
```

### Q: 测试时 `/logs/runtime.log` 报错

已在 `Dockerfile` 中修复：`mkdir -p /logs && ...`

### Q: 输出目录为空

已修复 `runner.py` 的异常捕获，任务失败时会写入 `trace.json` 和失败原因，不会导致 benchmark 崩溃。

### Q: 提交后报 `platform (linux/arm64) does not match detected host platform`

原因：Mac（Apple Silicon）默认构建 arm64 镜像，但评测服务器是 amd64（x86_64）。

解决：构建时指定平台：

```bash
docker build --platform linux/amd64 -t team1194:v1 .
```

> 需要 Docker Desktop 开启 **Rosetta** 或 **Virtualization framework** 支持。可在 Docker Desktop → Settings → Features → 勾选 "Use Rosetta for x86/amd64 emulation"。
