#!/bin/sh
set -e

threads="${CLIPES_CPU_THREADS:-}"
if [ -z "$threads" ] && [ -r /sys/fs/cgroup/cpu.max ]; then
    read -r quota period < /sys/fs/cgroup/cpu.max || true
    case "$quota" in
        ''|*[!0-9]*) ;;
        *)
            case "$period" in
                ''|0|*[!0-9]*) ;;
                *) threads=$(( quota / period )) ;;
            esac
            ;;
    esac
fi
if [ -z "$threads" ]; then
    threads=$(nproc 2>/dev/null || echo 1)
fi
if [ "$threads" -lt 1 ] 2>/dev/null; then
    threads=1
fi
export OMP_NUM_THREADS="$threads"
export OPENBLAS_NUM_THREADS="$threads"

if [ "$#" -gt 0 ]; then
    case "$1" in
        pytest|python|python3|sh|bash|ffmpeg|ffprobe|yt-dlp)
            exec "$@"
            ;;
    esac
fi

exec python -m clipes "$@"
