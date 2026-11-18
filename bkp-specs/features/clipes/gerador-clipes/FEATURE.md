---
produto: clipes
feature: gerador-clipes
repos:
  - clipes-lives
issues:
  - mvp-vod
status: em-andamento
tags:
  - clipes
  - live
  - vod
  - whisper
  - ffmpeg
  - gta-vi
relacionadas: []
---

# Feature: gerador de clipes de lives

## Resumo
Do link de uma live (YouTube/Twitch/Kick, ao vivo ou VOD) gera clipes 9:16 legendados dos melhores momentos (hype GTA VI, reações, sustos, comédia), 100% local em CPU e grátis. Sinais (chat, áudio, heatmap, palavras-chave) acham os picos; a transcrição com timestamps de palavra define bordas com sentido; um LLM local pequeno só refina borda e título.

## Onde vive
- `clipes-lives/` (GitHub privado `RomuuloGoncalves/clipes-lives`, branch `master`): pacote Python `clipes/` (ingest, signals, candidates, asr, snap, render, eval, cli), `Dockerfile` + `docker-compose.yml`, `tests/` (12 pytest), `docs/teste-real.md`.
- Pesquisa: `pesquisa/` (esta pasta): `pesquisa-github.md`, `pesquisa-tecnologias.md`, `agy/*.md`.

## As-built (MVP enxuto, 2026-11-17)
Implementado fora do plano T0-T37, num recorte menor, em Docker (nada no host): `docker compose run --rm clipes <url> [--max-clips N] [--live-minutes N]`. Saída em `~/Downloads/clipes-lives/<video_id>/` (clip_NN.mp4 1080x1920, clips.json, README).
- Feito: VOD e live (grava N min e processa), sinais loudness + heatmap "Most Replayed" (chat só em teste unitário), faster-whisper small int8 só nas janelas dos picos, bordas por palavra/pausa, render com fundo desfocado + legenda ASS por palavra, métricas (MidWordCutRate, SentenceEdgeRate, PeakPosition).
- Teste real: VOD 2h41 (UBv07VGeD24) = 8 clipes em 489 s com cache (ASR 703 s na 1ª vez, render 484 s); live de 4 min (cStJTQf9XUk) = 2 clipes em 360 s.
- Desvios: sem sharkyclips (pipeline próprio), sem LLM (título por heurística), sem CED-mini/chat real, sem janela deslizante contínua, sem Kick/Twitch testados, sem Gradio/index.html.
- Fora de escopo (decisão do usuário): publicação nas redes e camadas anti-shadowban.

## Decisões que ainda valem
- AD-001: sinais detectam picos; LLM nunca gera timestamp livre, só escolhe índices de frase (schema JSON + fallback heurístico).
- AD-002: live = gravação segmentada + pipeline de VOD em janela deslizante (5 min / 10 min).
- AD-003: VOD baixa áudio + chat inteiros e vídeo só dos trechos escolhidos.
- AD-004: sem React; saída em pasta + `index.html`; Gradio só na fase final.
- AD-005: spike T0 com sharkyclips (MIT, já implementa quase todo o design) decide wrapper vs pipeline próprio.

## Issues
- [mvp-vod](issues/mvp-vod/spec.md): spec, design, tasks do MVP VOD + live (P2) + extras (P3). Perguntas abertas em `issues/mvp-vod/context.md`.
