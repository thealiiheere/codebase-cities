# Codebase Cities

**Turn a GitHub profile into a 3D city you can explore and hear.**

Enter a username and every public repository becomes a building: floors are commits,
districts are languages, lit windows show recent work, and bridges link the most-forked
repos. The same data becomes music, one instrument per language. Drag the year slider to
watch the city grow from the first commit to today while the music evolves with it.

The same profile always produces the same city and the same tune: everything is derived
deterministically from the data.

![The demo city at night: lit windows mark recently active repos](docs/screenshots/city-night.jpg)

| By day | Flying through time (2021) | Building a city |
| --- | --- | --- |
| ![The demo city by day](docs/screenshots/city-day.jpg) | ![The same city at the end of 2021, still growing](docs/screenshots/timeline-2021.jpg) | ![Progress while a city is fetched and built](docs/screenshots/building.jpg) |

## How GitHub data becomes a city

| GitHub | City | Music |
| --- | --- | --- |
| A public, non-fork repo | A building on its language's district | |
| Commits on the default branch | Floors (1 to 33, on a log scale) | |
| Repo size | Footprint (log scale) | |
| Primary language | District and building color (GitHub's own language colors) | An instrument (top 5 languages) |
| Share of code bytes | | That instrument's volume |
| Commits in the last 90 days | Share of floors with lit windows at night | |
| Stars | Rooftop lights | |
| Forks | Glowing bridges between the most-forked repos of neighboring districts | |
| Rhythm of daily contributions | | Tempo and swing: steady is calm and even, bursty is faster and looser |
| Recent contributions | | Intensity: how many notes play |
| Username | Building styles | Key and scale |

Bigger districts sit closer to the central plaza. On the timeline every building keeps its
plot: a repo appears the year it was created and gains floors as its commits accumulate.

## Try it

**Without a backend:** run the frontend (below) and open http://localhost:5173/?demo for a
city built from a made-up profile, timeline and music included.

**The full app, with Docker:**

```bash
cp .env.example .env        # then set GITHUB_TOKEN (any token; public data needs no scopes)
docker compose up --build
```

Open http://localhost:5173 and enter a GitHub username. Share links look like
`/city/octocat`, or `/city/octocat?year=2015` for a point on the timeline.

## Architecture

```mermaid
flowchart LR
    subgraph Browser
        UI["Vite + TypeScript app"]
        Three["Three.js city"]
        Tone["Tone.js music"]
    end

    subgraph Server
        Web["nginx (production)<br/>or Vite (dev)"]
        API["FastAPI<br/>(api/ → services/)"]
        Worker["ARQ worker<br/>ingestion + mapping"]
        PG[("PostgreSQL<br/>users, repos,<br/>daily commits, jobs")]
        Redis[("Redis<br/>job queue, city cache,<br/>GitHub ETags")]
    end

    GitHub["GitHub API<br/>GraphQL + REST"]

    UI --> Three & Tone
    UI -- "/api/..." --> Web --> API
    API -- "read cities, jobs" --> PG
    API -- "enqueue jobs, cached cities" --> Redis
    API -- "user exists? (REST + ETag)" --> GitHub
    Redis -- "jobs" --> Worker
    Worker -- "profile, repos, history,<br/>contributions (GraphQL)" --> GitHub
    Worker -- "store normalized data" --> PG
    Worker -- "cache the built city" --> Redis
```

What happens when you enter a username:

```mermaid
sequenceDiagram
    participant B as Browser
    participant A as API
    participant W as Worker
    participant G as GitHub
    B->>A: POST /api/cities/octocat
    alt fetched in the last 12 hours
        A-->>B: 200 + the city
    else needs fetching
        A->>G: does octocat exist? (cheap, ETag-cached)
        A-->>B: 202 + job (or 404 / 503)
        W->>G: profile, repos, commit history, calendar
        W->>W: store in Postgres, build the city, cache it in Redis
        loop every second
            B->>A: GET /api/jobs/{id}
            A-->>B: queued → fetching → building → done
        end
        B->>A: GET /api/cities/octocat
        A-->>B: the city and music (from the cache)
    end
    B->>A: GET /api/cities/octocat?until=2019-12-31 (year slider)
```

### Backend (`backend/`, Python 3.12)

| Package | What it does |
| --- | --- |
| `app/api/` | FastAPI routers, kept thin: they validate input and call services |
| `app/services/` | Orchestration: requesting cities, job bookkeeping, the ingestion job, serving and caching cities |
| `app/github/` | Async GitHub client: GraphQL for data (commit history batched 10 repos per query), REST with ETags for profiles, rate limit handling, retries |
| `app/ingestion/` | Fetch → normalize (pure) → store (one transaction) |
| `app/mapping/` | **Pure functions**: data → city layout and music. No I/O, no clock, no global randomness; seeded per username. |
| `app/models/`, `migrations/` | SQLAlchemy 2.0 async models and Alembic migrations |
| `app/worker.py` | ARQ worker settings and the ingestion job |

**Determinism** is enforced by tests: the same input gives byte-identical output, input order
doesn't matter, two processes with different `PYTHONHASHSEED` agree, a golden digest pins the
output (bump `MAPPING_VERSION` when changing it on purpose), and a guard test fails if the
mapping package imports anything that does I/O or reads the clock.

**Caching:** rendered cities live in Redis under keys that include the mapping version and
the fetch time, so a new algorithm or fresh data never serves a stale city. GitHub REST
responses are cached with ETags (unchanged ones cost no rate limit).

### Frontend (`frontend/`, Vite + TypeScript)

| File | What it does |
| --- | --- |
| `src/main.ts` | Wires the page: routes, loading, timeline, music, hover |
| `src/api.ts` | Typed client mirroring the backend's response models; job polling |
| `src/city/` | Three.js: floors drawn as instanced meshes (one draw call for every floor), ground and districts, bridges, day/night lighting |
| `src/sound/` | Tone.js: synthesized instruments and the player (switches music on bar lines) |
| `src/timeline.ts`, `src/sources.ts` | Year slider and fly-through; cached per-year cities from the API or the demo files |
| `src/router.ts` | Share links (`/city/{username}?year=`) and browser history |

## Development

### Backend (from `backend/`)

```bash
uv sync                                  # install dependencies (uv fetches Python 3.12)
uv run pytest                            # tests (integration tests skip without databases)
uv run ruff check . && uv run ruff format --check .
uv run mypy app tests migrations scripts
uv run alembic upgrade head              # apply migrations
uv run alembic revision --autogenerate -m "describe change"
```

Tests marked `integration` need real Postgres and Redis and are skipped unless
`TEST_DATABASE_URL` and `TEST_REDIS_URL` are set (CI always sets them). They **wipe** that
database and Redis between tests, so use a separate test database. With the compose stack
running:

```bash
docker compose exec postgres createdb -U cities cities_test
TEST_DATABASE_URL=postgresql+asyncpg://cities:cities@localhost:5432/cities_test TEST_REDIS_URL=redis://localhost:6379/1 uv run pytest
```

Tests never call GitHub. To check the client against the real API with your token:

```bash
uv run python -m scripts.github_smoke octocat
```

After changing the mapping engine, regenerate the demo city and its timeline:

```bash
uv run python -m scripts.export_demo_city
```

### Frontend (from `frontend/`)

```bash
npm install
npm run dev          # http://localhost:5173, forwards /api to http://localhost:8000
npm run build        # strict type check + production bundle
```

If a Postgres already runs on your machine's port 5432, set `POSTGRES_HOST_PORT=5433` in
`.env` (and the same port in `DATABASE_URL`) so the dev stack doesn't collide with it.

## API

| Request | Result |
| --- | --- |
| `POST /api/cities/{username}` | `200` + the city when stored data is fresh; `202` + a job (and `Location` header) when it must be fetched; `404` for unknown users; `429` (code `rate_limited`, with `Retry-After`) when this client has asked for too many new cities; `503` when GitHub is rate limited (with `Retry-After`) |
| `GET /api/cities/{username}?until=YYYY-MM-DD` | The city layout and music, optionally as of the end of a past day; `404` until the city has been built once |
| `GET /api/jobs/{job_id}` | The job: `queued`, `fetching`, `building`, `done`, or `failed` (with a readable `error`) |
| `GET /api/health` | `200` when Postgres and Redis respond, else `503` |

Errors always look like `{"error": {"code": "...", "message": "..."}}`. Interactive docs are
at `/docs` on the API.

**Rate limit.** By default each client IP (each /64 network for IPv6) may make 10 city requests
an hour that reach GitHub: new cities, refreshes of outdated ones, and lookups of unknown users.
Serving a fresh city, joining a job that is already running, polling a job, and reading a city
(including the timeline) are never counted. Counts live in Redis, so they hold across API
processes; if Redis is down, requests are let through and a warning is logged. Set
`RATE_LIMIT_NEW_CITIES` and `RATE_LIMIT_WINDOW_SECONDS` in `.env` (0 turns the limit off).

## Deployment

The production stack runs anywhere with Docker (a VPS, or any container host that runs
Compose):

```bash
cp .env.example .env     # set GITHUB_TOKEN and a strong POSTGRES_PASSWORD
docker compose -f docker-compose.prod.yml up -d --build
```

- `web` (nginx) serves the built site on `WEB_PORT` (default 80) and forwards `/api` to the
  API. It also serves `index.html` for share links like `/city/octocat`.
- `migrate` applies database migrations once; the API and worker start after it succeeds.
- Postgres and Redis are only reachable inside Docker's network, with data in named volumes
  (`pgdata`, `redisdata`). Back up `pgdata` like any Postgres database (`pg_dump`).
- **HTTPS:** terminate TLS in front of `WEB_PORT`, with your platform's load balancer or a
  reverse proxy. For example, with [Caddy](https://caddyserver.com) on the same server:
  `caddy reverse-proxy --from cities.example.com --to localhost:80` (certificates are automatic).
- **Updating:** `git pull`, then the same `docker compose ... up -d --build`.

CI builds this exact stack and smoke-tests it through nginx on every push.

### On Render

[`render.yaml`](render.yaml) is a Render Blueprint that creates everything from this repo:

| Service | Render type | Plan in the Blueprint |
| --- | --- | --- |
| `codebase-cities` (the website) | Static Site | free |
| `codebase-cities-api` | Web Service (Docker) | free (sleeps when idle) |
| `codebase-cities-worker` | Background Worker (Docker) | starter (no free plan for workers) |
| `codebase-cities-db` | Postgres 16 | free (time-limited by Render) |
| `codebase-cities-redis` | Key Value (Redis-compatible) | free |

1. Push this repository to GitHub (Render deploys from it).
2. In the [Render dashboard](https://dashboard.render.com): **New → Blueprint**, connect the
   repository, and apply `render.yaml`. Paste your `GITHUB_TOKEN` when asked (it goes to the
   API and the worker).
3. When the deploy finishes, check the API's URL. If it isn't
   `https://codebase-cities-api.onrender.com`, put the real one in the `/api/*` rewrite of the
   website in `render.yaml`, then push.
4. Open the website's URL and build a city. Then look at the API's logs: each request shows the
   visitor's address. If every request shows the same address, set `FORWARDED_ALLOW_IPS` to
   `*` on the API service, or the rate limit would count all visitors as one.

Every push to `main` redeploys automatically; migrations run when the API starts. Plans and
prices change, so check [render.com/pricing](https://render.com/pricing). Before keeping
real data, upgrade the free Postgres, because Render removes free databases after a while.

## Known limitations

- **History is approximate.** GitHub has no cheap star, fork, size, or language history, so the
  timeline keeps today's values for those and only replays repos and commits. Commit history is
  capped per repo (newest 5,000 by default); older totals are exact, their dates are not.
- **Public data only.** Private repos never appear, even when the token can see them.
- **The rate limit is per IP address.** It stops one visitor from spending your token's quota,
  but not many addresses working together, and visitors who share an address (an office
  network, say) share one limit. It's a fixed window, so a client can fit up to twice the
  limit around the moment one window ends and the next begins. The API finds the visitor's
  address in `X-Forwarded-For`, trusting only proxies on private networks (see
  `docker-compose.prod.yml`). Behind a proxy on a public address, such as a CDN, add its
  ranges there, or every visitor will count as that proxy.
- **Bundle size.** Three.js and Tone.js make the frontend about 220 KB gzipped.
