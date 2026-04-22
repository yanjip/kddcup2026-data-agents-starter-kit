FROM python:3.11

WORKDIR /app

RUN pip install uv

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --no-dev

COPY . .

RUN uv build

ENTRYPOINT ["sh", "-c", "uv run dabench \"$@\" 2>&1 | tee /logs/runtime.log", "--"]
CMD ["--help"]
