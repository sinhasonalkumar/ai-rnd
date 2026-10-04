#!/usr/bin/env bash
# Stop the server and any background embedding job.
pkill -f "uvicorn app:app" && echo "server stopped" || true
pkill -f "python indexer.py" && echo "embedding paused (resume with ./start.sh)" || true
