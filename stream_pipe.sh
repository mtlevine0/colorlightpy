#!/usr/bin/env bash

source .venv/bin/activate

PIPE=/tmp/mypipe
rm -f "$PIPE"
mkfifo -m 666 "$PIPE"

trap 'rm -f "$PIPE"; exit' INT TERM

echo "Starting display with auto-reconnecting pipe reader..."
echo "Pipe: $PIPE"
echo ""

# Python opens and monitors the pipe directly - no stdin needed!
python main.py --pipe "$PIPE" -i enp12s0 -W 384 -H 192 --fps 27 \
    --stdin-timeout 0.1 --no-signal-threshold 20

echo "Shutting down..."
rm -f "$PIPE"
