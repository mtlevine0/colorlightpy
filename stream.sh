#!/bin/bash
# Create pipe
rm /tmp/video_pipe
mkfifo /tmp/video_pipe

# Start receiver
source .venv/bin/activate
sudo .venv/bin/python main.py stream --pipe /tmp/video_pipe -i enp12s0 -W 384 -H 192 --fps 30 --pixel-format bgr &
RECEIVER_PID=$!

# Wait for receiver to be ready
sleep 1

VIDEO=$1
START_TIME=$2

# Start both ffmpeg processes at the same time
{
    ffmpeg -y -re -ss $START_TIME -stream_loop -1 -i $VIDEO -an -vf scale=384:192 -f rawvideo -pix_fmt rgb24 /tmp/video_pipe &
    VIDEO_PID=$!
    
    ffmpeg -re -ss $START_TIME -stream_loop -1 -i $VIDEO -vn -f pulse default &
    AUDIO_PID=$!
    
    wait $VIDEO_PID $AUDIO_PID
}

# Cleanup
kill $RECEIVER_PID
rm /tmp/video_pipe