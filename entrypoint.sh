#!/bin/sh
set -e

if [ "$#" -gt 0 ]; then
    case "$1" in
        pytest|python|python3|sh|bash|ffmpeg|ffprobe|yt-dlp)
            exec "$@"
            ;;
    esac
fi

exec python -m clipes "$@"
