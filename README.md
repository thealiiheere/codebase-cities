# Codebase Cities

Turns a GitHub profile into a 3D city you can explore and hear. See [CLAUDE.md](CLAUDE.md) for the
full design and build phases. (A proper README with screenshots arrives in phase 8.)

## Status

Phase 1 (foundation): project scaffolding, config, database models and migrations, health
endpoint, CI.

## Running locally

### With Docker (recommended)

```bash
cp .env.example .env        # then fill in GITHUB_TOKEN
docker compose up --build
```

The API is on http://localhost:8000. Check it with http://localhost:8000/api/health, and browse the
interactive docs at http://localhost:8000/docs. Migrations run automatically on startup.

### Backend tools (from `backend/`)

```bash
uv sync                          # install dependencies (uv fetches Python 3.12 if needed)
uv run pytest                    # tests
uv run ruff check . && uv run ruff format --check .
uv run mypy app tests migrations
uv run alembic upgrade head      # apply migrations (needs Postgres running)
uv run alembic revision --autogenerate -m "describe change"   # new migration from model changes
```

Tests marked `integration` need a real Postgres and are skipped unless `TEST_DATABASE_URL` is set
(CI always sets it). With the compose stack running:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://cities:cities@localhost:5432/cities uv run pytest
```

Note that the integration test drops and recreates all tables in that database.
