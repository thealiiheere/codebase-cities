FROM python:3.12-slim

# uv, copied from its official image, pinned to the version used locally.
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /bin/

# The virtualenv lives outside /app so that bind-mounting the source in docker-compose (for live
# reload) does not hide it. UV_COMPILE_BYTECODE speeds up container start.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first, in their own layer, so code changes don't reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
