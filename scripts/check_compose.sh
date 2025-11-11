#!/usr/bin/env bash
# Quick helper to build, start and smoke-test the docker-compose stack locally.
# Run from repository root: ./scripts/check_compose.sh
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

echo "Building and starting compose stack (detached)..."
docker compose up -d --build

# wait for controller health
echo "Waiting for controller (http://localhost:8000/health) to become healthy..."
for i in {1..30}; do
  if curl -sS http://localhost:8000/health >/dev/null 2>&1; then
    echo "controller is up"
    break
  fi
  sleep 1
  echo -n "."
done

# wait for workers
for port in 8001 8002 8003 8004; do
  echo
  echo "Waiting for worker on port $port..."
  for i in {1..30}; do
    if curl -sS "http://localhost:${port}/health" >/dev/null 2>&1; then
      echo " worker:$port up"
      break
    fi
    sleep 1
    echo -n "."
  done
done

echo
# Quick put/get check through worker1 (should succeed if controller+workers are registered):
KEY="smoke_key"
VALUE="smoke_value"

echo "PUTing $KEY->$VALUE to worker1..."
set +e
HTTP_PUT=$(curl -s -o /dev/stderr -w "%{http_code}" -X PUT "http://localhost:8001/kv/${KEY}" -H 'Content-Type: application/json' -d "{\"value\": \"${VALUE}\"}")
set -e
if [ "$HTTP_PUT" != "200" ] && [ "$HTTP_PUT" != "201" ]; then
  echo "PUT returned HTTP $HTTP_PUT — check logs: docker compose logs --tail=200"
  exit 1
fi

echo "GETting $KEY from worker1..."
curl -sS "http://localhost:8001/kv/${KEY}" | jq || true

echo
echo "Smoke test complete. If any checks failed, run: docker compose logs controller worker1 worker2 worker3 worker4 --tail=200"

echo "To stop the stack: docker compose down -v"
