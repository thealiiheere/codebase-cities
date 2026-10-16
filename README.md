# Codebase Cities

Turns a GitHub profile into a 3D city you can explore and hear. See [CLAUDE.md](CLAUDE.md) for the
full design and build phases. (A proper README with screenshots arrives in phase 8.)

## Status

- Phase 1 (foundation): project scaffolding, config, database models and migrations, health
  endpoint, CI.
- Phase 2 (GitHub client): async GraphQL + REST client with rate limit handling, ETags, and
  retries, in `backend/app/github/`.
- Phase 3 (ingestion and jobs): ARQ worker, fetch/normalize/store pipeline, job tracking, and
  the endpoints below.

## API so far

| Request | Result |
| --- | --- |
| `POST /api/cities/{username}` | `202` + job (and `Location` header) when data must be fetched; `200` when stored data is fresh; `404` for unknown users; `503` when GitHub is rate limited (with `Retry-After`) |
| `GET /api/jobs/{job_id}` | The job: `status` is `queued`, `fetching`, `building`, `done`, or `failed` (with an `error` message) |
| `GET /api/health` | `200` when Postgres and Redis respond, else `503` |

Errors always look like `{"error": {"code": "...", "message": "..."}}`.

## Running locally

### With Docker (recommended)

```bash
cp .env.example .env        # then fill in GITHUB_TOKEN
docker compose up --build
```

The API is on http://localhost:8000. Check it with http://localhost:8000/api/health, and browse the
interactive docs at http://localhost:8000/docs. Migrations run automatically on startup. The
`worker` service runs ingestion jobs; restart it after changing backend code
(`docker compose restart worker`), since unlike the API it does not auto-reload.

### Backend tools (from `backend/`)

```bash
uv sync                          # install dependencies (uv fetches Python 3.12 if needed)
uv run pytest                    # tests
uv run ruff check . && uv run ruff format --check .
uv run mypy app tests migrations scripts
uv run alembic upgrade head      # apply migrations (needs Postgres running)
uv run alembic revision --autogenerate -m "describe change"   # new migration from model changes
```

Tests marked `integration` need real Postgres and Redis and are skipped unless
`TEST_DATABASE_URL` and `TEST_REDIS_URL` are set (CI always sets them). They **wipe** that
database and Redis between tests, so point them at a separate test database, never your dev data.
With the compose stack running, create one once and use Redis database 1:

```bash
docker compose exec postgres createdb -U cities cities_test
TEST_DATABASE_URL=postgresql+asyncpg://cities:cities@localhost:5432/cities_test TEST_REDIS_URL=redis://localhost:6379/1 uv run pytest
```

### Checking the GitHub client against the real API

Tests never call GitHub, so they can't catch a wrong GraphQL field name. With `GITHUB_TOKEN` in
`.env`, run this from `backend/` to exercise every client method once:

```bash
uv run python -m scripts.github_smoke octocat
```
