#!/bin/sh
# Start command for Render's free plan, which has no background workers: this one container
# runs both the ARQ worker and the API. (docker-compose.prod.yml runs them separately.)
set -e

alembic upgrade head

# The worker runs in the background. If it ever exits (say, Redis restarted), start it again.
(
  while true; do
    arq app.worker.WorkerSettings || true
    echo "Worker exited; restarting in 5 seconds" >&2
    sleep 5
  done
) &

# exec: uvicorn takes this shell's place, so it receives Render's stop signal directly.
exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers
