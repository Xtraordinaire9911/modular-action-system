# Builder stage: install dependencies and run the same checks as CI.
FROM python:3.11-slim AS builder

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY evaluation/ ./evaluation/
COPY scripts/ ./scripts/
COPY tests/ ./tests/
COPY config/ ./config/
COPY artifacts/ ./artifacts/
COPY run_demo.py ./

RUN pip install --upgrade pip && \
    pip install -e ".[dev]"

RUN ruff check . && \
    black --check . && \
    mypy src/ --ignore-missing-imports && \
    pytest --tb=short -q && \
    python run_demo.py

# Runner stage: minimal Python image for the runtime smoke entry point.
FROM python:3.11-slim AS runner

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY evaluation/ ./evaluation/
COPY config/ ./config/
COPY artifacts/ ./artifacts/
COPY run_demo.py ./

RUN pip install --upgrade pip && \
    pip install -e "."

CMD ["python", "-m", "src.pipeline"]
