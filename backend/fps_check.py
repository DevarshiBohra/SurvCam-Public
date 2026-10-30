#!/usr/bin/env python3
"""
fps_check.py - measures how many frames per second an MJPEG stream really delivers.

Usage:
    python3 fps_check.py "<stream url>" [seconds]

Example (through your backend, which already holds the camera connection):
    python3 fps_check.py "http://127.0.0.1:8000/live/cam001?access_token=$TOKEN" 60

Prints the average fps plus the slowest and fastest second, so stutter shows up too.
Needs only python3 (no ffmpeg).

TOKEN="PASTE_YOUR_TOKEN_HERE"
python3 ./fps_check.py "http://127.0.0.1:8000/live/CAMERA_ID_1?access_token=$TOKEN" 60 & \
python3 ./fps_check.py "http://127.0.0.1:8000/live/CAMERA_ID_2?access_token=$TOKEN" 60 & \
wait
"""

import sys
import time
import urllib.request
from urllib.parse import urlparse

JPEG_START = b"\xff\xd8\xff"   # every JPEG frame begins with these bytes


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    url = sys.argv[1]
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    label = urlparse(url).path or url          # never print the access token

    try:
        response = urllib.request.urlopen(url, timeout=15)
    except Exception as exc:
        print(f"{label}: could not open stream: {exc}")
        sys.exit(2)

    per_second = [0] * (seconds + 1)
    total = 0
    tail = b""
    start = time.time()

    try:
        while True:
            elapsed = time.time() - start
            if elapsed >= seconds:
                break
            chunk = response.read(4096)
            if not chunk:
                break
            data = tail + chunk
            found = data.count(JPEG_START)
            tail = data[-2:]          # a marker is 3 bytes, so it cannot hide in this overlap twice
            total += found
            per_second[int(elapsed)] += found
    except Exception as exc:
        print(f"{label}: stream stopped early: {exc}")

    elapsed = time.time() - start
    if elapsed < 1 or total == 0:
        print(f"{label}: no frames received")
        sys.exit(3)

    full = per_second[: int(elapsed)]          # only seconds that were completely measured
    print(
        f"{label}: {total} frames in {elapsed:.1f}s = {total / elapsed:.1f} fps"
        f"   (slowest second {min(full)}, fastest second {max(full)})"
    )


if __name__ == "__main__":
    main()