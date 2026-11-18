---
produto: clipes-lives
feature: legenda-hashtags
repos: [clipes-lives]
issues: []
status: implementada (validação final pendente)
tags: [llm, cpu, qwen3, llama-cpp, hashtags, caption]
relacionadas: [partes-por-assunto, creditos-encerramento, deteccao-por-cota]
---

# legenda-hashtags

## Resumo
Usa o LLM local `Qwen3-1.7B-Q4_K_M` via binário `llama-server` (`/opt/llama`, com `/no_think`, ChatML e gramática JSON Schema) para gerar `titulo` (`<= 60` caracteres, `?`/`!`, `<= 1` emoji, validado contra alucinação de nomes próprios), `legenda` de 3 linhas, `3–5` `hashtags` específicas (validadas contra hífens e tags proibidas `#fyp/#viral/#reels/#foryou`, apoiadas por banco de hashtags por nicho/jogo/streamer) e `nota` `0–10` usada apenas como desempate relativo.

## Onde vive
- `clipes-lives`: `clipes/asr.py` (liberação explícita de RAM do Whisper), `clipes/llm.py` (novo), `clipes/cli.py`.

## Decisões que valem
- Whisper e LLM **nunca** coexistem na RAM: `asr.py` descarrega o Whisper antes; `llama-server` sobe uma vez por lote após o ASR e encerra ao final do lote.
- Sem abstração: uma função `complete(prompt, schema) -> dict` lendo `CLIPES_LLM_GGUF` (permite trocar para `Qwen3-4B-Instruct-2507` por env var).
- Nota `0–10` do LLM nunca descarta clipe na v1; serve apenas como desempate/ranking secundário e registro em `clips.json`.
