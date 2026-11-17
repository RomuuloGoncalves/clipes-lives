# clipes-lives

Gerador gratuito e 100% CPU-only de clipes verticais (`1080x1920`, H.264/AAC) com legendas queimadas para lives e VODs (YouTube e Twitch), rodando integralmente em Docker.

## Requisitos

- Apenas **Docker** e **Docker Compose** (nada instalado no host, sem GPU, sem chaves de API ou login).

## Uso

Construir a imagem:

```sh
docker compose build
```

Processar um VOD ou live ativa (saída padrão em `~/Downloads/clipes-lives/<video_id>/`, personalizável via `OUT_DIR`):

```sh
# VOD (YouTube / Twitch)
docker compose run --rm clipes "https://www.youtube.com/watch?v=UBv07VGeD24"

# Live ativa (grava N minutos do stream e processa o arquivo gravado)
docker compose run --rm clipes "https://www.youtube.com/watch?v=cStJTQf9XUk" --live-minutes 4 --max-clips 2
```

Opções da CLI:

```sh
docker compose run --rm clipes <url> \
  --max-clips 8 \
  --min-dur 25 \
  --max-dur 55 \
  --live-minutes 20 \
  --chat   # ou --no-chat (lives e VODs sem chat degradam sem erro)
```

Se um vídeo ou live contínua (ex.: só música) não gerar picos/clipes válidos, o pipeline encerra normalmente (código `0`), grava `clips.json` vazio com o motivo e imprime aviso claro.

Rodar a suíte de testes dentro do container:

```sh
docker compose run --rm clipes pytest
```

## Saída

Em `${OUT_DIR:-~/Downloads/clipes-lives}/<video_id>/`:

- `clip_01.mp4` ... `clip_NN.mp4` (1080x1920, vídeo centralizado sobre fundo desfocado + legendas `.ass`)
- `clips.json` (score, sinais que dispararam, início/fim, título, transcrição e métricas)
- `README.md` (resumo da execução)

## Limpeza

Para remover container, imagem e volume de cache de modelos:

```sh
docker compose down --rmi all -v
```
