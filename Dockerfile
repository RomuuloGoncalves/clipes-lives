FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    HOME=/cache \
    XDG_CACHE_HOME=/cache \
    HF_HOME=/cache/huggingface \
    OMP_NUM_THREADS=6 \
    OPENBLAS_NUM_THREADS=6

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    fonts-liberation \
    fontconfig \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -c "import platform, urllib.request, zipfile, io, os; \
arch = 'aarch64-unknown-linux-gnu' if platform.machine() in ('aarch64','arm64') else 'x86_64-unknown-linux-gnu'; \
data = urllib.request.urlopen(f'https://github.com/denoland/deno/releases/latest/download/deno-{arch}.zip', timeout=60).read(); \
zf = zipfile.ZipFile(io.BytesIO(data)); \
zf.extract('deno', '/usr/local/bin'); \
os.chmod('/usr/local/bin/deno', 0o755)"

WORKDIR /app

RUN pip install --upgrade pip && pip install \
    "yt-dlp[default]" \
    "faster-whisper>=1.1.0" \
    "numpy>=1.26.0" \
    "scipy>=1.12.0" \
    "pytest>=8.0.0"

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

COPY pyproject.toml /app/pyproject.toml
COPY clipes /app/clipes
COPY tests /app/tests

RUN mkdir -p /cache /out && chmod -R 0777 /cache /out

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
