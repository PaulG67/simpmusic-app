#!/bin/sh
set -e
PORT="${PORT:-5080}"
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
