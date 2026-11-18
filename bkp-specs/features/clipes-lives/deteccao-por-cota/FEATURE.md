---
produto: clipes-lives
feature: deteccao-por-cota
repos: [clipes-lives]
issues: []
status: implementada (validação final pendente)
tags: [signals, candidates, asr, quota, prosody, calibration]
relacionadas: [partes-por-assunto, legenda-hashtags]
---

# deteccao-por-cota

## Resumo
Elimina a dependência exclusiva de picos de áudio (`find_peaks`) e do heatmap do YouTube implementando a proposta da frente D: Passada 0 acústico-prosódica (blocos de `30s` filtrados por `speech_density >= 0.30` e estratificados por terços do vídeo), ASR em duas passadas (`faster-whisper small` com `beam_size=1` sem `word_timestamps` na Passada 1 até o orçamento de 30% da duração + `word_timestamps=True` na Passada 2 apenas nos candidatos aprovados), segmentação e pontuação determinística `CheapScore` por percentil do vídeo, seleção por cota/piso por hora (`--min-per-hour 3`, `--mode {peaks,quota,auto}`), diversificação temporal e deduplicação textual/temporal, além de calibração `precision@K` e medições M2/M7 no Ryzen 5 5500U.

## Onde vive
- `clipes-lives`: `clipes/signals.py`, `clipes/asr.py`, `clipes/topics.py`, `clipes/candidates.py`, `clipes/eval.py`, `clipes/cli.py`, `tests/fixtures/calib_labels.json`.

## Decisões que valem
- Entra nesta rodada por decisão explícita do usuário (em série após `partes-por-assunto` e `creditos-encerramento`).
- Rejeitado o uso de Whisper `tiny`/`base` na Passada 1 por WER elevado em pt-BR (~24% no FLEURS, CONSOLIDADO §5); a Passada 1 usa `small int8` já assado na imagem com `beam_size=1` e `word_timestamps=False`.
- Piso de qualidade anti-lixo: música contínua ou silêncio (`speech_density < 0.30` ou `< 30` palavras) continua emitindo `0` clipes.

## Estado (2026-11-17)
- Implementada e integrada: `--mode`, `--min-per-hour`, `--asr-budget-ratio` em `clipes/cli.py`; `tests/test_cli_quota.py`.
- Medição M2/M7: parcial. M7 (Passada 1) rodou: 96 blocos, 2880 s de áudio, `quota=8 floor=False`. A Passada 2 e o relatório `pesquisa/MEDICOES-M2-M7.md` não foram concluídos: o PC desligou duas vezes sob carga de 6 threads (sem OOM nem aviso térmico no log).
- Pendente: reexecutar a medição com `cpus: 3`, `nice` e log de temperatura; M1 (WER small x turbo) depende da transcrição de referência do usuário.
