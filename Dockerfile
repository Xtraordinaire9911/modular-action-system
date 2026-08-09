# Builder stage: install dependencies and run the same checks as CI.
FROM python:3.11-slim AS builder

WORKDIR /app

# Copy the whole context and let .dockerignore do the filtering. An explicit
# per-directory allowlist silently goes stale: any newly added top-level
# directory is missing inside the image, so this stage fails for a reason that
# never reproduces in the lint-test job (which sees the full checkout).
COPY . .

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

COPY . .

RUN pip install --upgrade pip && \
    pip install -e "."

CMD ["python", "-m", "src.pipeline"]
