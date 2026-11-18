---
produto: clipes-lives
feature: creditos-encerramento
repos: [clipes-lives]
issues: []
status: implementada (validação final pendente)
tags: [render, ffmpeg, ass, safe-zones, yunet]
relacionadas: [partes-por-assunto, legenda-hashtags]
---

# creditos-encerramento

## Resumo
Ajusta as safe zones da legenda no `.ass` (`MarginV=560`, `MarginL=120`, `MarginR=200`), queima o crédito do canal na tela (`twitch.tv/<canal>` ou `@<canal>` em `y=600`, forte nos primeiros `4.0s` e marca d'água a `40%` de opacidade até o fim), elimina o corte seco com congelamento de `1.2s` do último quadro (`tpad=stop_mode=clone:stop_duration=1.2`), áudio `loudnorm -> afade -> apad`, saída `-t D+1.2` e cartão final ASS (`"Parte k+1 →"` ou `"Live completa: twitch.tv/<canal>"`), além de reenquadramento vertical opcional (P2) a `1 fps` com YuNet ONNX.

## Onde vive
- `clipes-lives`: `clipes/models.py`, `clipes/ingest.py`, `clipes/render.py`, `clipes/reframe.py` (novo, P2 opcional), `clipes/cli.py`.

## Decisões que valem
- Serial com `partes-por-assunto` (compartilham `render.py` e `cli.py`).
- Ordem de filtros de áudio obrigatória: `loudnorm` primeiro, depois `afade` + `apad` (evita normalizar silêncio, CONSOLIDADO §2.3).
- Sem fade para preto no encerramento para não quebrar o loop nas plataformas.
- Layout 16:9 centralizado sobre fundo borrado é o padrão na v1; reenquadramento com YuNet é P2 opcional (`--reframe`).
