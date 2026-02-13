source .venv/bin/activate
# # # while true; do
# # socat -u PIPE:/tmp/mypipe STDOUT | python main.py --stdin -i enp12s0 -W 384 -H 192 --fps 27 --stdin-timeout 1.0

# PIPE=/tmp/mypipe
# mkfifo -m 666 "$PIPE" 2>/dev/null

# while true; do
#     echo "Waiting for writer..."

#     # This blocks until a writer connects
#     cat "$PIPE" | python main.py --stdin -i enp12s0 -W 384 -H 192 --fps 27 --stdin-timeout 2.0

#     echo "Writer disconnected. Restarting..."
#     sleep 2.0
# done

#!/usr/bin/env bash

PIPE=/tmp/mypipe
mkfifo -m 666 "$PIPE" 2>/dev/null

trap 'break' INT TERM

while true; do
    echo "Waiting for writer..."
    cat "$PIPE" | python main.py --stdin -i enp12s0 -W 384 -H 192 --fps 27 --stdin-timeout 0.1 --no-signal-threshold 200
    echo "Writer disconnected. Restarting..."
    sleep 0.2
done

echo "Shutting down..."
rm -f "$PIPE"
