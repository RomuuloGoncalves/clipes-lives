---
produto: clipes-lives
feature: monitor-twitch
repos: [clipes-lives]
issues: []
status: implementada (validação final pendente)
tags: [twitch, live, irc, streamlink, queue]
relacionadas: [partes-por-assunto, legenda-hashtags, creditos-encerramento]
---

# monitor-twitch

## Resumo
Monitora canais da Twitch (`channels.txt` padrão sem chave; Helix Get Streams opcional apenas se credenciais estiverem no env), lê o chat via IRC anônimo `justinfan` (`<= 80` canais, JOIN a cada `0.6s`), detecta pico relativo por canal (Poisson 10s), grava em buffer rolante de 5 min com `streamlink | ffmpeg -c copy` somente até 3 canais quentes + 1 sob demanda (pré-roll `180s`, pós-roll `60–120s`), enfileira em diretórios JSON com rename atômico e processa 1 job por vez no pipeline existente, sem publicar.

## Onde vive
- `clipes-lives`: `Dockerfile` (`streamlink`), `clipes/ingest.py` (`ingest_local`), `clipes/monitor.py` (novo), `clipes/cli.py`.

## Decisões que valem
- `channels.txt` é o padrão; Helix só com `TWITCH_CLIENT_ID` e `TWITCH_CLIENT_SECRET` em `os.environ` (nunca em arquivo). Proibido GQL interno para listar categoria.
- Limite duro de concorrência no piso: `<= 3` gravadores quentes + `1` sob demanda (`-c copy`), e `1` worker sequencial de processamento por vez (Whisper e LLM nunca juntos).
- `policy.json` controla `blocked`, `allowed` e `delay_h` por canal (ex.: `gaules: 24`). Sem publicação nesta rodada.
