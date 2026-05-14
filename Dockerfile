FROM python:3.11

WORKDIR /app

# 使用国内 pip 镜像源加速依赖下载
ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.aliyun.com \
    UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    UV_HTTP_TIMEOUT=120

RUN pip install uv

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --no-dev

COPY . .

RUN uv build

ENTRYPOINT ["sh", "-c", "mkdir -p /logs && uv run dabench \"$@\" 2>&1 | tee /logs/runtime.log", "--"]
CMD ["run-benchmark", "--config", "configs/submission.yaml"]
