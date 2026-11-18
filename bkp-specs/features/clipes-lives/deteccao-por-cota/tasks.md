# deteccao-por-cota Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path. The skill is the source of truth for the full flow (per-task cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user - do not proceed without it.**

---

**Design**: `/home/romulo/Projetos/planos_tlc-spec-driven/clipes-lives/features/clipes-lives/deteccao-por-cota/design.md`
**Status**: Approved

---

## Test Coverage Matrix

> Generated from codebase, project guidelines, and spec - confirm before Execute. Guidelines found: `README.md`, `pyproject.toml` (`docker compose run --rm clipes pytest`).

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| ---------- | ------------------ | -------------------- | ---------------- | ----------- |
| Prosódia / Triagem / ASR 2 passadas / `CheapScore` / Cota (`signals.py`, `asr.py`, `topics.py`, `candidates.py`, `eval.py`) | unit | Todas as branches; 1:1 com ACs da spec; piso anti-lixo, diversificação por terços e dedup textual | `tests/test_signals_candidates.py`, `tests/test_quota.py` | `docker compose run --rm clipes pytest` |
| CLI `--mode {peaks,quota,auto}` (`cli.py`) | integration | Execução fim a fim nos modos `peaks`, `quota` e `auto`, preservando `0` clipes em sinal plano/música | `tests/test_quota.py` | `docker compose run --rm clipes pytest -v` |
| Medições M2 e M7 no piso 5500U | integration | Execução real no container medindo `xRT` de `beam_size=1`, orçamento de 2 passadas e `precision@8` sem heatmap | `tests/test_bench_m2_m7_quota.py` | `docker compose run --rm clipes pytest -v -s` |

## Gate Check Commands

> Generated from codebase - confirm before Execute.

| Gate Level | When to Use | Command |
| ---------- | ----------- | ------- |
| Quick | Após tasks com testes unitários | `docker compose run --rm clipes pytest -q` |
| Full | Após tasks de integração ou benchmark M2/M7 | `docker compose run --rm clipes pytest -v` |
| Build | Após conclusão de fase | `docker compose build && docker compose run --rm clipes pytest -v` |

---

## Execution Plan

Phases are ordered and run sequentially - each phase completes before the next begins, and tasks within a phase execute in order. Todas as 7 tasks compõem o **Lote Série 3 (`deteccao-por-cota`)**, executando após `partes-por-assunto` e `creditos-encerramento`.

**Fase 1 — Sinal Prosódico, ASR em Duas Passadas e Pontuação de Segmentos**

```
T1 -> T2 -> T3
```

**Fase 2 — Seletor por Cota/Piso, CLI, Calibração e Medições M2/M7**

```
T3 -> T4 -> T5 -> T6 -> T7
```

---

## Task Breakdown

### Phase 1: Prosody Triage, Two-Pass ASR & Segment Scoring

### T1: [repo: clipes-lives] Implementar signal_prosody e triage_audio_blocks (Passada 0) em clipes/signals.py

**What**: Adicionar `signal_prosody(rms_db, n)` (desvio-padrão de voz ativa + modulação silábica `|Δ rms_db|` com `min_scale` adaptativo por IQR) e `triage_audio_blocks(bundle, block_s=30.0, hop_s=15.0, budget_ratio=0.30, min_speech_density=0.30)` estratificando os blocos pelos 3 terços do vídeo em `clipes/signals.py`.
**Where**: `clipes/signals.py`
**Depends on**: None
**Reuses**: `robust_percentile_norm`, `FPS_RMS`, `SILENCE_DB`, `compute_signals` em `clipes/signals.py`
**Requirement**: COTA-01, COTA-02

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [x] `signal_prosody` produz score `>= 0.5` em fala conversada com volume comprimido (`±2 dB`) e `0.0` em silêncio ou tom contínuo
- [x] `triage_audio_blocks` descarta blocos com `speech_density < 0.30`, distribui a seleção pelos 3 terços do vídeo e respeita a soma máxima `budget_ratio * duration`
- [x] Gate passa: `docker compose run --rm clipes pytest -q`
- [x] Test count: `>= 14` testes passam

**Tests**: unit
**Gate**: quick

---

### T2: [repo: clipes-lives] Implementar ASR em duas passadas (transcribe_fast_blocks beam=1 + Passada 2) em clipes/asr.py

**What**: Adicionar `transcribe_fast_blocks(audio_path, blocks, cache_dir, model_size=None, cpu_threads=None)` em `clipes/asr.py` usando `faster-whisper small` com `beam_size=1, word_timestamps=False, vad_filter=True` (cacheado em `pass1_segments.json`, liberando o modelo com `del model; gc.collect()`) e parametrizar `beam_size` em `transcribe_candidates` (Passada 2 com `word_timestamps=True`).
**Where**: `clipes/asr.py`
**Depends on**: T1
**Reuses**: `merge_windows`, `load_audio_slice`, `transcribe_candidates` em `clipes/asr.py`
**Requirement**: COTA-03

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [x] `transcribe_fast_blocks` funde blocos vizinhos, executa `model.transcribe(..., beam_size=1, word_timestamps=False)` e grava/reutiliza `pass1_segments.json`
- [x] Passada 2 (`transcribe_candidates`) roda `word_timestamps=True` apenas nas janelas dos candidatos aprovados
- [x] Gate passa: `docker compose run --rm clipes pytest -q`
- [x] Test count: `>= 16` testes passam

**Tests**: unit
**Gate**: quick

---

### T3: [repo: clipes-lives] Implementar score_topic_segments (CheapScore) e dedup_segments_text em clipes/topics.py

**What**: Adicionar `score_topic_segments(segments, bundle)` (calculando `hook`, `story_kw` com filtro negativo para leitura de sub/donate, `pace`, `prosody` e `ext`, normalizados por percentil dentro do próprio vídeo) e `dedup_segments_text(segments, max_jaccard=0.60)` em `clipes/topics.py`.
**Where**: `clipes/topics.py`
**Depends on**: T2
**Reuses**: `TopicSegment` em `clipes/models.py`, `robust_percentile_norm` em `clipes/signals.py`, `TITLE_KEYWORDS` em `clipes/render.py`
**Requirement**: COTA-04

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [x] Segmentos com pergunta/exclamação inicial, ritmo conversacional e reação pontuam acima de trechos monótonos ou de agradecimento de sub/donate
- [x] `dedup_segments_text` remove segmentos com Jaccard de bigramas `> 0.60` preservando o de maior score
- [x] Gate passa: `docker compose run --rm clipes pytest -q`
- [x] Test count: `>= 19` testes passam

**Tests**: unit
**Gate**: quick

---

### Phase 2: Quota Selection, CLI Integration, Calibration & M2/M7 Benchmarks

### T4: [repo: clipes-lives] Implementar pick_quota_candidates com modo piso e diversificação em clipes/candidates.py

**What**: Implementar `pick_quota_candidates(bundle, topic_segments, max_clips=8, min_per_hour=3.0, min_separation=120.0, floor_percentile=75.0, min_abs_score=0.25)` em `clipes/candidates.py`, combinando picos fortes (`>= P90`) e segmentos pontuados, aplicando relaxamento de percentil `P75 -> P65 -> P55` (`floor_mode`), piso anti-lixo (`word_count >= 30`, `score >= 0.25`) e teto de `ceil(K / 2)` candidatos por terço do vídeo no 1º passe.
**Where**: `clipes/candidates.py`
**Depends on**: T3
**Reuses**: `pick_candidate_peaks`, `CandidatePeak` em `clipes/candidates.py`
**Requirement**: COTA-05

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [x] Em vídeo de 2h sem picos fortes, `pick_quota_candidates` retorna `6` candidatos (`min_per_hour=3.0`) com espaçamento `>= 120s` e no máximo `3` no mesmo terço
- [x] Em vídeo sem fala (`topic_segments == []` ou `score < 0.25`), retorna `([], False)` sem fabricar clipes de silêncio/música
- [x] Gate passa: `docker compose run --rm clipes pytest -q`
- [x] Test count: `>= 22` testes passam

**Tests**: unit
**Gate**: quick

---

### T5: [repo: clipes-lives] Integrar flags --mode, --min-per-hour e --asr-budget-ratio em clipes/cli.py

**What**: Adicionar `--mode {peaks,quota,auto}` (padrão `auto`), `--min-per-hour` (padrão `3.0`) e `--asr-budget-ratio` (padrão `0.30`) em `clipes/cli.py`, orquestrando Passada 0 -> Passada 1 -> `score_topic_segments` -> `pick_quota_candidates` -> Passada 2 e gravando `detection_mode`, `floor_mode` e `quota_target` em `clips.json`.
**Where**: `clipes/cli.py`
**Depends on**: T4
**Reuses**: `run_pipeline` em `clipes/cli.py`
**Requirement**: COTA-05

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [x] `--mode auto` aciona a seleção por cota quando os picos fortes não preenchem `ceil(min_per_hour * dur_h)`
- [x] `test_zero_clips_flat_signal_degrades_cleanly` continua passando com código `0` e `clips == []`
- [x] Escopo verde: `pytest -q tests/test_cli_quota.py tests/test_cli_llm.py tests/test_render_eval.py::test_zero_clips_flat_signal_degrades_cleanly` (9 passed). A suite completa fica para o coordenador.

**Tests**: integration
**Gate**: full

---

### T6: [repo: clipes-lives] Adicionar evaluate_quota_calibration em clipes/eval.py e fixture calib_labels.json

**What**: Implementar `evaluate_quota_calibration(selected, labels, k=8)` (`precision_at_k`, `recall_at_k` por IoU ou inclusão do centro em `[start, end]`) em `clipes/eval.py` e criar o conjunto rotulado `tests/fixtures/calib_labels.json` a partir dos momentos positivos e negativos de `UBv07VGeD24` e `cStJTQf9XUk`.
**Where**: `clipes/eval.py`
**Depends on**: T5
**Reuses**: `evaluate_clips` em `clipes/eval.py`, dados de `docs/teste-real.md`
**Requirement**: COTA-06

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [ ] `tests/fixtures/calib_labels.json` contém `>= 20` momentos rotulados — PENDENTE T7: fixture tem os 10 positivos documentados em docs/teste-real.md; negativos reais não existem no documento e não foram inventados
- [ ] `precision_at_8 >= 0.625` com `heatmap` ocultado — PENDENTE T7: a função existe e foi medida à parte (picos só com loudness deram 0.25; o ASR da Passada 1 sobre 30% do VOD não rodou)
- [x] Gate passa: `docker compose run --rm clipes pytest -q` (122 passed, 1 deselected)
- [x] Test count: `>= 26` testes passam (122)

**Tests**: unit
**Gate**: full

---

### T7: [repo: clipes-lives] Executar medições M2 e M7 (ASR 2 passadas beam=1 + cota no Ryzen 5 5500U)

**What**: Criar e executar `tests/test_bench_m2_m7_quota.py` no container (`docker compose run --rm clipes pytest tests/test_bench_m2_m7_quota.py -v -s`) medindo o tempo e qualidade de `small` com `beam_size=1` (M2) e o pipeline completo de 2 passadas + cota (M7) no Ryzen 5 5500U.
**Where**: `tests/test_bench_m2_m7_quota.py`
**Depends on**: T6
**Reuses**: `clipes/asr.py`, `clipes/candidates.py`, `clipes/eval.py`
**Requirement**: COTA-07

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [ ] Medição M2 comprova no container que `small` com `beam_size=1, word_timestamps=False` atinge `xRT <= 0.38` e que `beam_size=1, word_timestamps=True` na Passada 2 mantém `sentence_edge_rate >= 0.90` e `mid_word_cut_rate == 0.0`
- [ ] Medição M7 comprova tempo total de ASR (Passada 1 + Passada 2) `<= 12.0 min` por hora de VOD e `precision_at_8 >= 0.625` sem usar o `heatmap`
- [ ] Gate passa: `docker compose run --rm clipes pytest -v`
- [ ] Test count: `>= 27` testes passam

**Tests**: integration
**Gate**: full

---

## Phase Execution Map

```
Phase 1:  T1 -> T2 -> T3
Phase 2:  T3 -> T4 -> T5 -> T6 -> T7
```

---

## Task Granularity Check

| Task | Scope | Status |
| ---- | ----- | ------ |
| T1: `signal_prosody` + `triage_audio_blocks` | 2 funções em `clipes/signals.py` | ✅ Granular |
| T2: `transcribe_fast_blocks` (Passada 1) | 1 função em `clipes/asr.py` | ✅ Granular |
| T3: `score_topic_segments` + `dedup_segments_text` | 2 funções em `clipes/topics.py` | ✅ Granular |
| T4: `pick_quota_candidates` | 1 função em `clipes/candidates.py` | ✅ Granular |
| T5: Flags `--mode`, `--min-per-hour`, `--asr-budget-ratio` | 1 arquivo (`clipes/cli.py`) | ✅ Granular |
| T6: `evaluate_quota_calibration` + `calib_labels.json` | 1 função em `clipes/eval.py` + fixture JSON | ✅ Granular |
| T7: Medições M2 e M7 no 5500U | 1 arquivo de benchmark (`tests/test_bench_m2_m7_quota.py`) | ✅ Granular |

---

## Diagram-Definition Cross-Check

| Task | Depends On (task body) | Diagram Shows | Status |
| ---- | ---------------------- | ------------- | ------ |
| T1 | None | Início da cadeia | ✅ Match |
| T2 | T1 | `T1 -> T2` | ✅ Match |
| T3 | T2 | `T2 -> T3` | ✅ Match |
| T4 | T3 | `T3 -> T4` | ✅ Match |
| T5 | T4 | `T4 -> T5` | ✅ Match |
| T6 | T5 | `T5 -> T6` | ✅ Match |
| T7 | T6 | `T6 -> T7` | ✅ Match |

---

## Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
| ---- | --------------------------- | --------------- | --------- | ------ |
| T1: Prosódia e triagem | Domain (`signals.py`) | unit | unit | ✅ OK |
| T2: ASR duas passadas | Domain (`asr.py`) | unit | unit | ✅ OK |
| T3: `CheapScore` e dedup | Domain (`topics.py`) | unit | unit | ✅ OK |
| T4: Seletor por cota | Domain (`candidates.py`) | unit | unit | ✅ OK |
| T5: Integração CLI | CLI (`cli.py`) | integration | integration | ✅ OK |
| T6: Calibração `precision@K` | Domain (`eval.py`) | unit | unit | ✅ OK |
| T7: Medições M2 e M7 | Benchmark M2/M7 | integration | integration | ✅ OK |
