---
produto: clipes-lives
feature: partes-por-assunto
repos: [clipes-lives]
issues: []
status: implementada (validação final pendente)
tags: [asr, snap, topics, minilm, render]
relacionadas: [creditos-encerramento, deteccao-por-cota]
---

# partes-por-assunto

## Resumo
Amplia a janela de transcrição ao redor do pico (`[pico-150s, pico+60s]`), detecta fronteiras semânticas de assunto com embeddings `paraphrase-multilingual-MiniLM-L12-v2` (ONNX int8) combinados a pausas `>= 0.8s`, e gera clipe único (`25–65s`) ou divide assuntos longos (`65–180s`) em 2–3 partes de `45–58s` de conteúdo (`Parte k/N`) cortadas estritamente em bordas de frase.

## Onde vive
- `clipes-lives`: `clipes/models.py`, `clipes/candidates.py`, `clipes/topics.py` (novo), `clipes/snap.py`, `clipes/render.py`, `clipes/eval.py`, `clipes/cli.py`.

## Decisões que valem
- Teto de `58.0s` de conteúdo por parte (com `+1.2s` de cartão final = `<= 60.0s`, evitando bloqueio global de Shorts `> 1 min` com claim de Content ID).
- Gancho de fim de parte na v1 = corte em borda de frase + selo `Parte k/N` + cartão final `"Parte k+1 →"`. Sem teaser/flash-forward nem recap.
- Fallback determinístico para fronteiras de pausa longa (`>= 0.8s`) + fim de frase caso o modelo ONNX não carregue.
