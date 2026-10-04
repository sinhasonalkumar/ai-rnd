#!/usr/bin/env bash
# Stop the server and any background embedding job.
pkill -f "uvicorn app:app" && echo "server stopped" || true
pkill -f "python indexer.py" || true
echo "embedding (if unfinished) resumes on next ./start.sh"
