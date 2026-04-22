# Docker 打包与提交指南

本指南详细说明如何将 KDD Cup 2026 DataAgent 解决方案打包成 Docker 镜像并提交。

## 目录

1. [前提条件](#1-前提条件)
2. [创建 Dockerfile](#2-创建-dockerfile)
3. [构建 Docker 镜像](#3-构建-docker-镜像)
4. [本地测试运行](#4-本地测试运行)
5. [导出镜像为压缩包](#5-导出镜像为压缩包)
6. [上传到 Google Drive](#6-上传到-google-drive)
7. [发送提交邮件](#7-发送提交邮件)
8. [常见问题](#8-常见问题)

---

## 1. 前提条件

### 1.1 安装 Docker

**macOS:**

```bash
# 使用 Homebrew 安装
brew install --cask docker

# 或者从官网下载 Docker Desktop
# https://www.docker.com/products/docker-desktop
```

安装完成后，启动 Docker Desktop 应用。

**验证安装:**

```bash
docker --version
docker ps
```

### 1.2 项目结构

确保项目结构如下：

```
kddcup2026-data-agents-starter-kit/
├── Dockerfile          # 需要创建
├── pyproject.toml
├── configs/
│   └── react_baseline.example.yaml
├── data/               # 数据目录（本地测试用）
└── ...
```

---

## 2. 创建 Dockerfile

在项目根目录下创建 `Dockerfile`（无扩展名）：

```dockerfile
FROM python:3.11

WORKDIR /app

RUN pip install uv

COPY pyproject.toml ./
RUN uv sync --frozen --no-dev

COPY . .

RUN uv build

ENTRYPOINT ["uv", "run", "dabench"]
CMD ["--help"]
```

### 2.1 Dockerfile 说明

| 指令 | 说明 |
|------|------|
| `FROM python:3.11` | 基于标准 Python 镜像 |
| `WORKDIR /app` | 设置工作目录 |
| `RUN pip install uv` | 安装 uv 包管理器 |
| `COPY pyproject.toml ./` | 复制依赖文件 |
| `RUN uv sync` | 安装项目依赖 |
| `COPY . .` | 复制所有代码 |
| `ENTRYPOINT` | 设置入口命令 |

### 2.2 多阶段构建（可选，推荐）

如果想减小镜像体积：

```dockerfile
FROM python:3.11 as builder

WORKDIR /app
RUN pip install uv
COPY pyproject.toml .
RUN uv sync --frozen --no-dev

FROM python:3.11

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

COPY . .

ENTRYPOINT ["python", "-m", "dabench"]
```

---

## 3. 构建 Docker 镜像

### 3.1 基本构建命令

```bash
docker build -t <team_id>:v<N> .
```

**示例：**

```bash
# 团队 ID 为 team0042，第一次提交
docker build -t team0042:v1 .

# 构建时指定 team_id 和版本号
docker build --build-arg TEAM_ID=team0042 --build-arg VERSION=v1 -t team0042:v1 .
```

### 3.2 使用 BuildKit 加速构建（推荐）

```bash
DOCKER_BUILDKIT=1 docker build -t team0042:v1 .
```

### 3.3 验证镜像构建成功

```bash
docker images | grep team0042
```

输出应类似：

```
team0042   v1   abc123def456   2 minutes ago   1.2GB
```

---

## 4. 本地测试运行

### 4.1 准备测试数据目录

创建测试用的目录结构：

```bash
# 创建测试输入目录
mkdir -p /tmp/eval/input/task_001
mkdir -p /tmp/eval/output
mkdir -p /tmp/eval/logs

# 复制一个示例任务到输入目录
cp -r data/public/input/task_11/* /tmp/eval/input/task_001/
```

### 4.2 本地运行容器

```bash
docker run --rm \
#   --network=eval_net \
#   --cpus=4 \
#   --memory=8g \
  -v /home/yjp/data/kdd_data/input:/input:ro \
  -v /home/yjp/data/kdd_data/output:/output:rw \
  -v /home/yjp/data/kdd_data/logs:/logs:rw \
  -e MODEL_API_URL=https://dashscope.aliyuncs.com/compatible-mode/v1 \
  -e MODEL_API_KEY="sk-c940fe1e2114483eb5bb753a18e5814d" \
  -e MODEL_NAME=qwen3.5-35b-a3b \
  team0042:v1 \
  run-benchmark --config configs/react_baseline.example.yaml --limit 1
```

```bash
docker run --rm \
  -v /home/yjp/data/kdd_data/input:/input:ro \
  -v /home/yjp/data/kdd_data/output:/output:rw \
  -v /home/yjp/data/kdd_data/logs:/logs:rw \
  -e MODEL_API_URL=https://dashscope.aliyuncs.com/compatible-mode/v1 \
  -e MODEL_API_KEY="sk-xx" \
  -e MODEL_NAME=qwen3.5-35b-a3b \
  team0042:v3
```
### 4.3 命令行参数说明

| 参数 | 说明 |
|------|------|
| `--rm` | 容器运行完毕后自动删除 |
| `--cpus=4` | 限制 CPU 核心数（本地测试用4核） |
| `--memory=8g` | 限制内存（本地测试用8G） |
| `-v /input:ro` | 挂载输入目录（只读） |
| `-v /output:rw` | 挂载输出目录（读写） |
| `-v /logs:rw` | 挂载日志目录（读写） |
| `-e MODEL_API_URL` | 模型 API 地址 |
| `-e MODEL_API_KEY` | 模型 API 密钥 |
| `-e MODEL_NAME` | 模型名称 |
数据卷挂载
```
-v /tmp/eval/input:/input:ro \
-v /tmp/eval/output:/output:rw \
-v /tmp/eval/logs:/logs:rw
```
通过卷挂载实现主机与容器的数据共享：

1. /tmp/eval/input:/input:ro :
   
   - 主机目录 /tmp/eval/input 挂载到容器内 /input
   - :ro 表示只读模式，防止容器修改输入数据
2. /tmp/eval/output:/output:rw :
   
   - 主机目录 /tmp/eval/output 挂载到容器内 /output
   - :rw 读写模式，容器生成的结果会持久化到主机
3. /tmp/eval/logs:/logs:rw :
   
   - 主机目录 /tmp/eval/logs 挂载到容器内 /logs
   - 容器运行日志会同步保存到主机，方便后续排查问题
### 4.4 检查输出

```bash
ls -la /tmp/eval/output/
ls -la /tmp/eval/logs/
```

---

## 5. 导出镜像为压缩包

### 5.1 导出命令

```bash
docker save team0042:v1 | gzip > team0042_v1.tar.gz
```

### 5.2 验证压缩包

```bash
ls -lh team0042_v1.tar.gz
```

### 5.3 重新加载镜像（测试用）

```bash
docker load -i team0042_v1.tar.gz
```

---

## 6. 上传到 Google Drive

### 6.1 上传文件

1. 打开 Google Drive (drive.google.com)
2. 点击「+ 新建」→ 「文件上传」
3. 选择 `team0042_v1.tar.gz` 文件

### 6.2 设置分享权限

1. 右键点击上传的文件
2. 选择「共享」→ 「获取链接」
3. 点击「链接类型」选择「anyone with the link」
4. 权限选择「查看者」
5. 点击「复制链接」

### 6.3 链接格式

复制的链接格式应为：

```
https://drive.google.com/file/d/FILE_ID/view?usp=share_link
```

其中 `FILE_ID` 是 Google Drive 分配的文件 ID。

---

## 7. 发送提交邮件

### 7.1 邮件信息准备

```
收件人: kddcup@hkust-gz.edu.cn
主题: [KDDCup2026 Data Agents] Submission - <team_id> - v<N>

Team ID: <team_id>
Version: v<N>
Sharing link: https://drive.google.com/file/d/FILE_ID/view?usp=share_link
```

### 7.2 完整示例

```
收件人: kddcup@hkust-gz.edu.cn
主题: [KDDCup2026 Data Agents] Submission - team0042 - v1

Team ID: team0042
Version: v1
Sharing link: https://drive.google.com/file/d/1QTBRom51ejitPLe9Ke_HKi1PZkAyWKOg/view?usp=share_link
```

### 7.3 提交检查清单

- [ ] 镜像名称格式正确：`<team_id>:v<N>`
- [ ] 压缩包文件名格式正确：`<team_id>_v<N>.tar.gz`
- [ ] Google Drive 链接设置为「Anyone with the link can view」
- [ ] 链接在整个评估期间保持有效
- [ ] 不要删除或修改 Google Drive 上的文件，直到收到评估完成通知
- [ ] 邮件主题格式正确

---

## 8. 常见问题

### Q1: Docker 构建失败怎么办？

**A:** 检查以下几点：

1. 确保 Docker Desktop 已启动
2. 检查 `pyproject.toml` 是否存在
3. 确保所有依赖都能正常安装
4. 查看具体的错误信息：

```bash
docker build -t team0042:v1 . --progress=plain
```

### Q2: 容器内无法访问模型 API？

**A:** 本地测试时使用本地模型服务URL。正式提交后，评估系统会注入正确的环境变量。

### Q3: 镜像体积太大怎么办？

**A:** 使用多阶段构建减少体积：

```dockerfile
FROM python:3.11 as builder
# ... 构建阶段 ...

FROM python:3.11
COPY --from=builder /app /app
# ... 只复制必要文件 ...
```

### Q4: 如何查看正在运行的容器？

```bash
docker ps -a
```

### Q5: 如何停止正在运行的容器？

```bash
docker stop <container_id>
```

### Q6: 清理未使用的 Docker 资源

```bash
# 清理未使用的镜像
docker image prune -a

# 清理未使用的容器、网络
docker system prune -a
```

### Q7: 版本号命名规则？

**A:** 版本号从 v1 开始，每次提交递增：

- 第一次提交：`team0042:v1` → `team0042_v1.tar.gz`
- 第二次提交：`team0042:v2` → `team0042_v2.tar.gz`
- 第三次提交：`team0042:v3` → `team0042_v3.tar.gz`

### Q8: 可以使用非 Python 基础镜像吗？

**A:** 可以，Dockerfile 中的基础镜像没有限制，只要能在容器内运行项目代码即可。

---

## 快速参考命令汇总

```bash
# 1. 构建镜像
docker build -t team0042:v1 .

# 2. 本地测试
docker run --rm \
  -v $(pwd)/data/public/input:/input:ro \
  -v $(pwd)/artifacts:/output:rw \
  -e MODEL_API_URL=http://localhost:8000/v1 \
  -e MODEL_API_KEY=test_key \
  -e MODEL_NAME=qwen3.5-35b-a3b \
  team0042:v1 \
  run-benchmark --config configs/react_baseline.example.yaml --limit 1

# 3. 导出镜像
docker save team0042:v1 | gzip > team0042_v1.tar.gz

# 4. 重新加载镜像（用于验证）
docker load -i team0042_v1.tar.gz

# 5. 查看镜像列表
docker images

# 6. 删除本地镜像
docker rmi team0042:v1
```

---

## 注意事项

1. **不要修改 `/input` 目录**：容器运行时 `/input` 是只读挂载
2. **确保 ENTRYPOINT 设置正确**：评估系统会直接运行 `docker run team0042:v1`
3. **输出目录必须可写**：`/output` 和 `/logs` 需要有写权限
4. **版本号递增**：每次提交必须使用新的版本号，不能重复使用旧版本号

---

如有问题，请访问：
- 官方网站：https://dataagent.top
- Discord：https://discord.com/invite/7eFwJQN3Fx
