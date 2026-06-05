# ── builder stage: installs dev deps, runs lint + tests ─────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip && \
    pip install -e ".[dev]"

COPY . .

RUN ruff check . && \
    black --check . && \
    pytest --tb=short -q

# ── runner stage: minimal production image ───────────────────────────────────
FROM python:3.11-slim AS runner

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip && \
    pip install -e "."

COPY src/ ./src/

CMD ["python", "-m", "src.pipeline"]
