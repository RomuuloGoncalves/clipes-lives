# Teste Real: `clipes-lives`

## 1. Alvo e Ambiente

- **URL testada:** `https://www.youtube.com/watch?v=UBv07VGeD24`
- **Título:** `BISTECONE & GABRIEL COXINHA - Podpah #1049`
- **Tipo:** VOD de live finalizada (`is_live: false`, `was_live: true`), duração `9659.0s` (~2h41min)
- **Sinais disponíveis:** `loudness` (RMS dB 1 Hz) + `heatmap` (`Most Replayed` do YouTube). Chat replay indisponível no VOD (`live_chat` ausente — degradou normalmente).
- **Hardware do host:** AMD Ryzen 5 5500U (12 threads), ~9.1 GiB RAM; container limitado a `6g` RAM (`mem_limit: 6g`, `CLIPES_CPU_THREADS=6`), rodando como `1000:1000`.
- **Saída:** `/home/romulo/Downloads/clipes-lives/UBv07VGeD24/`

## 2. Comandos Executados

```sh
# Build e testes unitários no container
docker compose build
docker compose run --rm clipes pytest -v

# Execução completa do pipeline no VOD alvo
docker compose run --rm clipes "https://www.youtube.com/watch?v=UBv07VGeD24" --max-clips 8
```

## 3. Tempos por Etapa (CPU)

| Etapa | Cold (1ª execução) | Cached (`audio.wav` + `words.json`) | Observação |
|---|---:|---:|---|
| `ingest` | ~15–25 s *(áudio 128k -> WAV 16k mono 294.8 MB)* | `3.77 s` | Reuso de `audio.wav` + checagem de metadados |
| `signals` | `0.62 s` | `0.62 s` | 9659 s de áudio (2 Hz RMS -> 1 Hz) + heatmap + `signals.npz` (177 KB) |
| `candidates` | `< 0.01 s` | `< 0.01 s` | 16 picos selecionados (`min_separation=90s`) |
| `asr` | `703.31 s` (~11.7 min) | `0.01 s` | `faster-whisper small` (`int8`, 6 threads) em 16 janelas de 90 s (24 min de áudio, 4444 palavras) |
| `snap` | `0.04 s` | `0.04 s` | 8/16 candidatos finais válidos após NMS e restrições |
| `render` | `484.42 s` (~8.1 min) | `484.42 s` | 8 clipes (~60.5 s/clipe: `yt-dlp --download-sections` + `ffmpeg` 1080x1920 + `.ass` + `loudnorm`) |

## 4. Clipes Gerados (`UBv07VGeD24`)

Métricas do conjunto final (`clips.json`):
- `count`: `8`
- `mid_word_cut_rate`: `0.0` (0 cortes no meio de palavras)
- `sentence_edge_rate`: `1.0` (100% das bordas em fim de frase `.?!` ou pausa `>= 0.5s`)
- `duration_ok_rate`: `1.0` (todos entre 25s e 55s)
- `peak_position_ok_rate`: `1.0` (todos com pico entre 35% e 75% do clipe)

| Clipe | Intervalo (Duração) | Score | Pico (`ratio`) | Sinais | Bordas (`ini`/`fim`) | Título / Motivo |
|---|---|---:|---:|---|---|---|
| `clip_01.mp4` | `305.29s–337.25s` (32.0s) | `0.902` | `327.0s` (`0.679`) | `loudness`, `heatmap` | `pause` / `pause` | *E pra vocês palmas em a é o gabriela caralho* — entrada do convidado com aplausos/gritos |
| `clip_02.mp4` | `1722.27s–1761.35s` (39.1s) | `0.764` | `1738.0s` (`0.403`) | `loudness`, `heatmap` | `sentence` / `sentence` | *Pera aí, cara!* — brincadeira sobre morar perto e encostar no pescoço |
| `clip_03.mp4` | `8436.26s–8483.18s` (46.9s) | `0.733` | `8453.0s` (`0.357`) | `heatmap`, `loudness` | `sentence` / `sentence` | *Quem inventar mais história falsa?* — debate sobre timidez e falar a verdade |
| `clip_04.mp4` | `8713.29s–8744.35s` (31.1s) | `0.703` | `8730.0s` (`0.538`) | `loudness`, `heatmap` | `sentence` / `sentence` | *O rugoso?* — piada sem filtro no final do episódio |
| `clip_05.mp4` | `3619.87s–3657.41s` (37.5s) | `0.675` | `3636.0s` (`0.430`) | `heatmap`, `loudness` | `sentence` / `sentence` | *Que que você perdeu?* — histórias de notebook e moto roubada |
| `clip_06.mp4` | `6010.29s–6038.52s` (28.2s) | `0.640` | `6026.0s` (`0.557`) | `loudness`, `heatmap` | `sentence` / `sentence` | *Cara, olha... Muito feliz.* — encontro no Rio de Janeiro |
| `clip_07.mp4` | `4764.57s–4810.23s` (45.7s) | `0.638` | `4782.0s` (`0.382`) | `heatmap`, `loudness` | `sentence` / `sentence` | *Olhando assim, ó.* — história do vizinho olhando pela janela |
| `clip_08.mp4` | `2362.60s–2392.85s` (30.2s) | `0.539` | `2379.0s` (`0.542`) | `loudness`, `heatmap` | `sentence` / `sentence` | *Quando eu peguei... Você chegava na varanda?* — história do auxílio e churrasco |

Todos os 8 arquivos MP4 foram validados via `ffprobe`: stream de vídeo `h264` `1080x1920`, stream de áudio `aac`, tamanhos entre `4.6 MB` e `16.4 MB`, pertencentes ao UID/GID `1000:1000`.

## 5. Problemas Encontrados e Correções Aplicadas

1. **YouTube EJS com Node 20.19.2 do Debian:** o `yt-dlp` 2026.10.26 rejeitou `node-20.19.2 (unsupported)`, causando falha de formato disponível no download do 3º segmento (`Requested format is not available`).
   - **Correção:** instalado `deno` oficial no `Dockerfile` e adicionado retry com backoff em `download_clip_window` (`render.py`).
2. **Cache do `words.json` não reutilizado:** `asr.py` comparava `list[tuple]` contra `list[list]` na chave `windows`.
   - **Correção:** normalização para tuplas arredondadas em `asr.py`, reduzindo a etapa de ASR em re-execuções de `703s` para `0.01s`.
3. **Reticências (`...`) como fim de frase e corte antes de reação sem fala:** na 1ª iteração, o `clip_01` terminou em `"coxinha do"` (`word_gap`) e o `clip_07` terminou em `"Mas ele..."`.
   - **Correções:** excluído `...`/`…` de `Word.ends_sentence` (`models.py`), reforçado peso de fim/início de frase vs `word_gap` (`snap.py`), e movido o filtro `t_e >= earliest` para *depois* de `_extend_past_reaction` (`snap.py`), permitindo que o `clip_01` encerrasse logo após os aplausos (`337.25s`, `sentence_edge_rate` subiu de `0.9375` para `1.0`).

## 6. Teste Real em Modo Live (`--live-minutes 4`)

### 6.1. Seleção da live ativa
- `https://www.youtube.com/watch?v=jfKfPfyJRdk` retornou erro do YouTube (`This live stream recording is not available`) no momento do teste (`2026-11-17 00:57 -03`).
- Busca de lives ativas no YouTube (`yt-dlp --flat-playlist`) encontrou transmissões em pt-BR no ar; foi selecionada a live com fala/gameplay `https://www.youtube.com/watch?v=cStJTQf9XUk` (*"GOTÃO TA ON! PRECISAMOS CONVERSAR! REACT & GAMEPLAY 🔴 YURI AO VIVO 🔴 2026-11-17 03:57"*, `is_live: true`).

### 6.2. Comando e Execução

```sh
docker compose run --rm clipes "https://www.youtube.com/watch?v=cStJTQf9XUk" --live-minutes 4 --max-clips 2
```

- **Gravação:** `ingest.py` detectou `is_live: true`, resolveu o stream via `yt-dlp -g` e gravou exatos `240.0 s` (4.0 min) em `/out/cStJTQf9XUk/.cache/live_source.mkv` (`36.4 MB`) + extraiu `audio.wav` (`7.3 MB`).
- **Degradação de sinais:** em modo live não há `heatmap` nem chat replay histórico (`[ingest] Live mode: skipping replay chat fetch.`); o pipeline rodou apenas com `loudness` (`weight=1.0`).
- **Tempos por etapa (`cStJTQf9XUk`):**
  - `ingest_s`: `245.82 s` (4 min de captura em tempo real + extração WAV)
  - `signals_s`: `0.02 s`
  - `candidates_s`: `0.00 s` (3 picos candidatos)
  - `asr_s`: `80.93 s` (`faster-whisper small` int8, 2 janelas mescladas, 616 palavras, `lang=pt`)
  - `snap_s`: `0.01 s` (2/3 candidatos válidos)
  - `render_s`: `33.27 s` (2 clipes cortados diretamente de `live_source.mkv`)
  - `total_s`: `360.05 s`

### 6.3. Clipes Gerados na Live (`/home/romulo/Downloads/clipes-lives/cStJTQf9XUk/`)

Métricas em `clips.json`: `count=2`, `mid_word_cut_rate=0.0`, `sentence_edge_rate=1.0`, `duration_ok_rate=1.0`, `peak_position_ok_rate=1.0`.

| Clipe | Intervalo (Duração) | Score | Pico (`ratio`) | Bordas | Título |
|---|---|---:|---:|---|---|
| `clip_01.mp4` | `14.09s–50.35s` (36.3s) | `0.684` | `30.0s` (`0.439`) | `sentence` / `sentence` | *Você iria contra o cauão, tio sã?* |
| `clip_02.mp4` | `109.71s–148.02s` (38.3s) | `0.321` | `124.0s` (`0.373`) | `sentence` / `sentence` | *Nossa, o muriloso vai matar esse filha da puta vê* |

Ambos os arquivos validados via `ffprobe`: `h264` `1080x1920`, `aac`, `2.4 MB` e `2.5 MB`, UID/GID `1000:1000`.

### 6.4. Ajuste pós-teste Live
- Verificado que, caso uma live contínua (ex.: só música ambiente) produza 0 picos ou 0 clipes válidos, o CLI agora grava `clips.json` (`"clips": []`, `"note": ...`) + `README.md` com mensagem clara e retorna código `0` sem falhar (`test_zero_clips_flat_signal_degrades_cleanly`).
- `record_live_stream` agora verifica a duração do `live_source.mkv` em cache antes de reaproveitar (se o usuário aumentar `--live-minutes`, regrava em vez de reutilizar trecho menor).

## 7. Limitações Conhecidas

- **Chat em lives ativas:** em modo `--live-minutes`, o chat ao vivo em tempo real ainda não é capturado (só VOD `live_chat` / Twitch GQL), degradando para `loudness` apenas.
- **Qualidade de transcrição em pt-BR (`small` int8):** gírias e sobreposição de vozes geram pequenos erros pontuais de grafia nas legendas/títulos (ex.: `"búlutinho"`), contornáveis passando `--whisper-model large-v3-turbo` ao custo de ~2x mais tempo de CPU e ~2.2 GB de RAM.
- **Títulos sem LLM:** a heurística prioriza frases com `!`/`?` e proximidade do pico; em trechos sem pontuação clara (como a abertura do `clip_01` no VOD), o título reflete literalmente a frase capturada pelo Whisper.
