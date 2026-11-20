FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    HOME=/cache \
    XDG_CACHE_HOME=/cache \
    HF_HOME=/cache/huggingface \
    HF_HUB_CACHE=/opt/models/hf \
    CLIPES_LLM_GGUF=/opt/models/llm/atual.gguf \
    CLIPES_EMBED_ONNX=/opt/models/embed/onnx/model_quint8_avx2.onnx \
    CLIPES_YUNET_ONNX=/opt/models/yunet/face_detection_yunet_2023mar.onnx \
    PATH=/opt/llama:$PATH \
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
    "yt-dlp[default,curl-cffi]" \
    "faster-whisper>=1.1.0" \
    "numpy>=1.26.0" \
    "scipy>=1.12.0" \
    "pytest>=8.0.0"

# Modelos assados na imagem (nada no host): `docker compose down --rmi all -v` remove tudo.
# ASR: whisper small (padrao) + large-v3-turbo (opcional, CLIPES_WHISPER_MODEL=large-v3-turbo)
RUN python3 -c "from faster_whisper import download_model as d; [d(m, cache_dir='/opt/models/hf') for m in ('small','large-v3-turbo')]"

# LLM local (Apache-2.0) + llama.cpp (binario CPU). WITH_LLM_4B=1 baixa e ativa o Qwen3-4B-Instruct-2507 (qualidade maior, ~4.5 GB de RAM).
ARG WITH_LLM_4B=0
RUN python3 -c "\
import platform, urllib.request, tarfile, io, os; \
from huggingface_hub import hf_hub_download as h; \
arch = 'arm64' if platform.machine() in ('aarch64','arm64') else 'x64'; \
data = urllib.request.urlopen(f'https://github.com/ggml-org/llama.cpp/releases/download/b12470/llama-b12470-bin-ubuntu-{arch}.tar.gz', timeout=120).read(); \
os.makedirs('/opt/llama', exist_ok=True); \
t = tarfile.open(fileobj=io.BytesIO(data)); \
[setattr(m, 'name', m.name.split('/', 1)[-1]) for m in t.getmembers()]; t.extractall('/opt/llama'); \
alvo = h('unsloth/Qwen3-1.7B-GGUF', 'Qwen3-1.7B-Q4_K_M.gguf', local_dir='/opt/models/llm'); \
alvo = h('unsloth/Qwen3-4B-Instruct-2507-GGUF', 'Qwen3-4B-Instruct-2507-Q4_K_M.gguf', local_dir='/opt/models/llm') if os.environ.get('WITH_LLM_4B') == '1' else alvo; \
link = '/opt/models/llm/atual.gguf'; \
os.path.lexists(link) and os.unlink(link); \
os.symlink(alvo, link)"

# Embeddings (segmentacao de assunto, ONNX int8, Apache-2.0) e detector de rosto YuNet (reenquadramento vertical)
RUN python3 -c "\
import urllib.request, os; \
from huggingface_hub import hf_hub_download as h; \
[h('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', f, local_dir='/opt/models/embed') for f in ('onnx/model_quint8_avx2.onnx','tokenizer.json','tokenizer_config.json','config.json')]; \
os.makedirs('/opt/models/yunet', exist_ok=True); \
urllib.request.urlretrieve('https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx', '/opt/models/yunet/face_detection_yunet_2023mar.onnx')"

# Dependencias das novas features (depois dos modelos para preservar o cache de camadas)
RUN pip install "onnxruntime>=1.18" "tokenizers>=0.19" "opencv-python-headless>=4.9" "streamlink>=7.0"
RUN pip install "pymongo>=4.8" "mongomock>=4.3"
RUN pip install "discord.py>=2.4,<3"

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

COPY pyproject.toml /app/pyproject.toml
COPY clipes /app/clipes
COPY painel /app/painel
COPY tests /app/tests
COPY assets /app/assets

RUN mkdir -p /cache /out && chmod -R 0777 /cache /out

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
