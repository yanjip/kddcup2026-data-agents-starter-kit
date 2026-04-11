FROM python:3.11-slim

WORKDIR /app

RUN pip install uv

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --no-dev

COPY . .

RUN uv build

ENTRYPOINT ["uv", "run", "dabench"]
CMD ["--help"]