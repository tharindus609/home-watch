#!/bin/bash
echo "source venv and starting home-watch"
source ~/src/home-watch/venv/bin/activate
nohup python3 home-watch.py &
PID=$!
echo "home-watch started with PID $PID and is running in the background."