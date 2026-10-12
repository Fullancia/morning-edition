#!/bin/sh
set -e
mkdir -p /data
# CUPS runs in the background; the app talks to it with lpadmin and lp.
cupsd
# Wait up to 10 seconds for the scheduler to start
for i in 1 2 3 4 5 6 7 8 9 10; do
  if lpstat -r 2>/dev/null; then
    break
  fi
  sleep 1
done
exec uvicorn morning_edition.web.app:create_app --factory --host 0.0.0.0 --port 8080
