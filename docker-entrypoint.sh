#!/bin/sh
set -e
PORT="${PORT:-5080}"
echo "Music Play lauscht auf 0.0.0.0:${PORT}"
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"

