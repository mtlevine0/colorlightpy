#!/usr/bin/env bash

source .venv/bin/activate

PIPE=/tmp/mypipe
rm -f "$PIPE"
mkfifo -m 666 "$PIPE"

trap 'rm -f "$PIPE"; exit' INT TERM

echo "Starting continuous stream (Ctrl+C to stop)..."

# Continuous reader that reopens pipe on disconnect
# This keeps feeding data to a single Python process
(
    while true; do
        cat "$PIPE" 2>/dev/null || sleep 0.1
    done
) | python main.py --stdin -i enp12s0 -W 384 -H 192 --fps 27 \
    --stdin-timeout 0.1 --no-signal-threshold 20

echo "Shutting down..."
rm -f "$PIPE"
