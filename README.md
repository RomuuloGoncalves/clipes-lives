# clipes-lives

Gerador gratuito e 100% CPU-only de clipes verticais (`1080x1920`, H.264/AAC) com legendas queimadas para lives e VODs (YouTube e Twitch), rodando integralmente em Docker.

## Requisitos

- Apenas **Docker** e **Docker Compose** (nada instalado no host, sem GPU, sem chaves de API ou login).

## Uso

Construir a imagem:

```sh
docker compose build
```

Processar um VOD ou live ativa. Use o `./clipar` (cria a pasta de saída como seu usuário; sem ele o Docker a cria como root, com cadeado). Saída padrão em `~/Downloads/clipes-lives/<AAAA-MM-DD - Título [id]>/`, personalizável via `OUT_DIR`:

```sh
# VOD (YouTube / Twitch)
./clipar "https://www.youtube.com/watch?v=UBv07VGeD24"

# Live ativa (grava N minutos do stream e processa o arquivo gravado)
./clipar "https://www.youtube.com/watch?v=cStJTQf9XUk" --live-minutes 4 --max-clips 2
```

O `./clipar` limita o container a 3 CPUs (`CLIPES_CPUS`, `CLIPES_CPU_THREADS`), roda com prioridade baixa e liga o `./vigia-temp`: ele grava a temperatura em `<saída>/.temp.log` a cada 5 s, pausa o container a 85 °C (`CLIPES_TEMP_MAX`) e retoma a 75 °C (`CLIPES_TEMP_MIN`). Para uso contínuo, `./clipar monitor` mantém o ciclo detectar, gravar e processar um job por vez; se parar, retoma da fila.

Opções da CLI:

```sh
docker compose run --rm clipes <url> \
  --max-clips 8 \
  --min-dur 25 \
  --max-dur 55 \
  --live-minutes 20 \
  --channel gaules \
  --reframe \
  --no-llm \
  --mode auto \
  --min-per-hour 3 \
  --asr-budget-ratio 0.30 \
  --chat   # ou --no-chat (lives e VODs sem chat degradam sem erro)
```

Detecção (`--mode`): `peaks` usa só os picos de áudio/chat/heatmap; `quota` transcreve rápido (Whisper small, beam 1) até `--asr-budget-ratio` da duração e escolhe os melhores trechos para garantir `--min-per-hour` clipes por hora; `auto` (padrão) usa picos e só aciona a cota quando os picos fortes não a preenchem. `--no-llm` desliga título/legenda/hashtags por IA local. O `clips.json` registra `detection_mode`, `floor_mode` e `quota_target`.

Variáveis de ambiente de renderização:

- `CLIPES_BLUR_BG` (padrão `0`): por padrão o vídeo 16:9 fica centralizado sobre barras pretas; `1` liga o fundo borrado (`split`/`boxblur`/`overlay`), que consome mais CPU.

Se um vídeo ou live contínua (ex.: só música) não gerar picos/clipes válidos, o pipeline encerra normalmente (código `0`), grava `clips.json` vazio com o motivo e imprime aviso claro.

Rodar a suíte de testes dentro do container:

```sh
docker compose run --rm test
```

## Saída

Em `${OUT_DIR:-~/Downloads/clipes-lives}/<AAAA-MM-DD - Título [id]>/` (reaproveita a pasta antiga só com o id, para não perder cache):

- `clip_01.mp4` ... `clip_NN.mp4` (1080x1920, vídeo centralizado sobre fundo desfocado + legendas `.ass`)
- `clips.json` (score, sinais que dispararam, início/fim, título, transcrição e métricas)
- `README.md` (resumo da execução)

## Limpeza

Para remover container, imagem e volume de cache de modelos:

```sh
docker compose down --rmi all -v
```
