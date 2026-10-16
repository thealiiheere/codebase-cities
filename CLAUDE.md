# Codebase Cities: Project Foundation

## What we're building

**Codebase Cities** turns a GitHub profile into a 3D city you can explore and hear.

A user enters a GitHub username. The backend fetches their public data, turns it into a deterministic city layout and a set of music parameters, and the frontend renders the city with Three.js and plays generated music with Tone.js.

- Repos become buildings. Height comes from commit count, footprint from repo size.
- Languages become districts and building colors.
- Stars become rooftop lights. Forks become bridges between buildings.
- Recent activity controls how lit the windows are at night.
- The same data drives the music. Each language is an instrument, commit rhythm sets the tempo, and overall activity sets intensity.
- A timeline slider lets you fly through the years while the city grows and the music evolves.

The same username must always produce the same city and the same music for the same data. Determinism is a core requirement.

## About me (the developer)

I'm a self-taught Python backend developer. The backend is where I want the real engineering quality: clean architecture, tests, proper async, caching, and job queues. The frontend is newer territory for me, so keep it simple, readable, and well commented.

## Tech stack

**Backend**
- Python 3.12, managed with `uv`
- FastAPI (async) with Pydantic v2
- PostgreSQL 16, using SQLAlchemy 2.0 (async) and Alembic for migrations
- Redis for caching and as the job queue broker
- ARQ for background jobs
- httpx (async) for GitHub API calls
- pytest, pytest-asyncio, and respx for mocking HTTP
- ruff for linting and formatting, mypy for type checking

**Frontend**
- Vite with vanilla TypeScript (no React)
- Three.js for the 3D city
- Tone.js for generated music

**Infrastructure**
- Docker Compose for local development (api, worker, postgres, redis, frontend)
- GitHub Actions CI running lint, type checks, and tests

## Architecture

```
codebase-cities/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app factory
│   │   ├── config.py            # Settings via pydantic-settings
│   │   ├── api/                 # Routers only, thin, no business logic
│   │   ├── github/              # GitHub client: GraphQL + REST, rate limits, ETags
│   │   ├── ingestion/           # Fetch → normalize → store pipeline
│   │   ├── mapping/             # PURE functions: data → city layout, data → music params
│   │   ├── models/              # SQLAlchemy models
│   │   ├── schemas/             # Pydantic request/response schemas
│   │   ├── services/            # Orchestration between layers
│   │   ├── cache.py             # Redis helpers
│   │   └── worker.py            # ARQ worker settings and job functions
│   ├── migrations/              # Alembic
│   └── tests/
├── frontend/
│   ├── src/
│   │   ├── main.ts
│   │   ├── api.ts               # Typed client for the backend
│   │   ├── city/                # Three.js scene, buildings, camera, lighting
│   │   └── sound/               # Tone.js instruments and sequencing
│   └── index.html
├── docker-compose.yml
└── .github/workflows/ci.yml
```

**Layer rules**
- `api/` calls `services/`. It never touches the database or GitHub directly.
- `mapping/` is pure: no I/O, no database, no network. It takes plain data and returns plain data. This is the heart of the project and must be heavily unit tested.
- `github/` knows nothing about the database.
- All randomness in `mapping/` uses a seeded RNG derived from a hash of the username, so the output is deterministic.

## Data flow

1. `POST /api/cities/{username}` checks the cache. If a fresh city exists, it returns it. Otherwise it enqueues an ingestion job and returns `202` with a job id.
2. The worker fetches the profile, repos, languages, and commit history from GitHub and stores normalized data in PostgreSQL.
3. The worker runs the mapping engine and caches the resulting city JSON and music JSON in Redis.
4. `GET /api/jobs/{job_id}` lets the frontend poll the status (`queued`, `fetching`, `building`, `done`, or `failed`).
5. `GET /api/cities/{username}` returns the city layout and music parameters. It accepts an optional `?until=YYYY-MM-DD` parameter for the timeline.

## GitHub data

- Use the GraphQL API where possible, since it needs fewer requests. Use REST only where GraphQL lacks the data.
- Authenticate with a `GITHUB_TOKEN` from the environment.
- Data needed per user: profile, public non-fork repos (name, stars, forks, size, primary language, language breakdown, created date, last push), commit counts per repo over time on the default branch, and the contributions calendar.
- Respect rate limits. Read the rate limit headers or GraphQL `rateLimit` field, back off when low, and surface a clear error when exhausted.
- Use ETags or conditional requests for REST calls to avoid wasting quota on unchanged data.
- Cap the work: at most 100 repos per user, sorted by most recently pushed. Make this configurable.
- Store commit history as daily aggregates per repo, not individual commits.

## Database (starting point; refine as needed)

- `github_users`: id, login (unique, lowercase), name, avatar_url, created_at, fetched_at
- `repos`: id, user_id, github_id, name, stars, forks, size_kb, primary_language, created_at, pushed_at
- `repo_languages`: repo_id, language, bytes
- `daily_commits`: repo_id, date, count (unique on repo_id + date)
- `jobs`: id, username, status, error, created_at, finished_at

## Mapping engine

**City layout**
- Group repos into districts by primary language. Arrange districts around a central plaza, with larger languages closer to the center.
- Within a district, place buildings on a grid with streets between blocks. No overlaps.
- Building height uses a log scale of commit count, clamped to a min and max so huge repos don't break the skyline.
- Building footprint scales with repo size, also log scaled and clamped.
- Color comes from the language. Keep one palette map in a single place.
- Window brightness (0 to 1) reflects activity in the last 90 days.
- Rooftop lights scale with stars. Bridges connect repos that have forks, linking the most-forked repos in neighboring districts.
- With `until`, include only repos created before that date and only commits up to that date.

**Music parameters**
- Tempo (BPM) comes from commit regularity. Steady daily commits mean a calm, steady tempo. Bursty patterns mean more variation.
- Each top language (max 5) maps to an instrument. Its share of total code bytes sets its volume.
- Musical key and scale are derived deterministically from the username hash.
- Intensity or density comes from recent activity.
- Output a structured JSON description (tempo, key, scale, tracks with instrument, volume, and note pattern). The frontend synthesizes it. The backend never produces audio files.

## API contract

All responses use Pydantic schemas. Errors use a consistent shape: `{"error": {"code": "...", "message": "..."}}`. Return a clear `404` for unknown GitHub users and `503` when the GitHub rate limit is exhausted.

## Build phases

Work through these in order. Finish one phase, make sure its tests pass, then stop and summarize before starting the next.

1. **Foundation:** project scaffolding, Docker Compose, config, database models, Alembic, health endpoint, CI, ruff, mypy, pytest setup.
2. **GitHub client:** async client with GraphQL + REST, rate limit handling, ETags, retries. Tested with mocked responses only.
3. **Ingestion and jobs:** ARQ worker, ingestion pipeline, job status tracking, the `POST` and job polling endpoints.
4. **Mapping engine:** city layout and music parameters as pure functions with thorough unit tests, including determinism tests (same input gives identical output).
5. **City API and caching:** `GET` city endpoint, Redis caching, the `until` timeline parameter.
6. **Frontend city:** Three.js scene with buildings, districts, camera controls, day/night lighting.
7. **Frontend sound:** Tone.js playback from the music JSON, with play/pause and volume.
8. **Timeline and polish:** year slider, share links (`/city/{username}`), loading states, README with screenshots and an architecture diagram, deployment.

## Working rules for Claude Code

- Before writing code in each phase, briefly state your plan and the files you'll create.
- Write tests alongside the code, not after. Never call the real GitHub API in tests.
- Use type hints everywhere and keep functions small.
- Don't add dependencies beyond the stack above without asking me first and explaining why.
- If something in this document seems wrong or there's a clearly better approach, tell me instead of silently deviating.
- Keep secrets in `.env` (with a committed `.env.example`). Never hardcode tokens.
- Explain non-obvious decisions in short comments, especially in the frontend, since that part is new to me.
- After each phase, give me a short summary: what was built, how to run it, and what's next.
