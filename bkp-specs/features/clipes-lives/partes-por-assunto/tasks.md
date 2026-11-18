# partes-por-assunto Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path. The skill is the source of truth for the full flow (per-task cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user - do not proceed without it.**

---

**Design**: `/home/romulo/Projetos/planos_tlc-spec-driven/clipes-lives/features/clipes-lives/partes-por-assunto/design.md`
**Status**: Approved

---

## Test Coverage Matrix

> Generated from codebase, project guidelines, and spec - confirm before Execute. Guidelines found: `README.md`, `pyproject.toml` (`pytest` in container via `docker compose run --rm clipes pytest`).

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| ---------- | ------------------ | -------------------- | ---------------- | ----------- |
| Domain / pipeline (`models.py`, `candidates.py`, `topics.py`, `snap.py`, `eval.py`) | unit | Todas as branches; 1:1 com ACs da spec; todos os edge cases listados | `tests/test_*.py` | `docker compose run --rm clipes pytest` |
| Render / CLI (`render.py`, `cli.py`) | integration | Geração de `.ass` com `PartBadge`, manifesto `clips.json` com partes e validação de métricas | `tests/test_render_eval.py` | `docker compose run --rm clipes pytest` |
| Benchmarks de hardware M1 e M4 | integration | Execução no container no piso 5500U com asserções numéricas de xRT, RSS, WER e recall de fronteira | `tests/test_bench_m1_wer.py`, `tests/test_bench_m4_topics.py` | `docker compose run --rm clipes pytest -v -s` |

## Gate Check Commands

> Generated from codebase - confirm before Execute.

| Gate Level | When to Use | Command |
| ---------- | ----------- | ------- |
| Quick | Após tasks com testes unitários | `docker compose run --rm clipes pytest -q` |
| Full | Após tasks de integração ou benchmark | `docker compose run --rm clipes pytest -v` |
| Build | Após conclusão de fase | `docker compose build && docker compose run --rm clipes pytest -v` |

---

## Execution Plan

Phases are ordered and run sequentially - each phase completes before the next begins, and tasks within a phase execute in order. Todas as 7 tasks compõem o **Lote Série 1** (início da série `partes-por-assunto -> creditos-encerramento -> deteccao-por-cota`).

**Fase 1 — Modelos, Janela Assimétrica e Segmentador de Assuntos**

```
T1 -> T2 -> T3
```

**Fase 2 — Snapping de Partes, Renderização de Selo e Medições M1/M4**

```
T3 -> T4 -> T5 -> T6 -> T7
```

---

## Task Breakdown

### Phase 1: Foundation & Topic Segmentation

### T1: [repo: clipes-lives] Estender dataclasses em clipes/models.py para partes e segmentos de assunto

**What**: Adicionar a dataclass `TopicSegment` e os campos `part_index: int = 1`, `part_total: int = 1`, `group_id: str = ""` em `SnappedClip` (além de `topic_start`/`topic_end` opcionais em `CandidatePeak`).
**Where**: `clipes/models.py`
**Depends on**: None
**Reuses**: `Word`, `CandidatePeak`, `SnappedClip` em `clipes/models.py`
**Requirement**: PART-03, PART-04

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [x] `TopicSegment` definido com propriedade `duration` e campos `start, end, peak, sentences, boundary_score`
- [x] `SnappedClip` possui `part_index=1`, `part_total=1`, `group_id=""` sem quebrar instâncias existentes
- [x] Testes unitários adicionados e gate passa: `docker compose run --rm clipes pytest -q`
- [x] Test count: `>= 10` testes passam (sem deleções silenciosas)

**Tests**: unit
**Gate**: quick

---

### T2: [repo: clipes-lives] Implementar janela assimétrica [pico-150s, pico+60s] em clipes/candidates.py

**What**: Atualizar `pick_candidate_peaks` para aplicar `pre_window: float = 150.0` e `post_window: float = 60.0` nos top `max_clips` candidatos (mantendo `window_half = 45.0` nos candidatos reserva).
**Where**: `clipes/candidates.py`
**Depends on**: T1
**Reuses**: `pick_candidate_peaks` em `clipes/candidates.py`
**Requirement**: PART-01

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [ ] Os primeiros `max_clips` candidatos retornados têm `start_win = max(0.0, peak - 150.0)` e `end_win = min(duration, peak + 60.0)`
- [ ] Candidatos de índice `>= max_clips` mantêm `peak ± window_half`
- [ ] Testes unitários para janela assimétrica adicionados em `tests/test_signals_candidates.py` e gate passa: `docker compose run --rm clipes pytest -q`
- [ ] Test count: `>= 11` testes passam

**Tests**: unit
**Gate**: quick

---

### T3: [repo: clipes-lives] Criar segmentador semântico de assuntos e divisor de partes em clipes/topics.py

**What**: Criar `clipes/topics.py` com `words_to_sentences`, `embed_sentences` (usando `onnxruntime` + `tokenizers` em `CLIPES_EMBED_ONNX`), `segment_topics` (fronteiras em vales `< media - 1.0*std` + pausa `>= 0.8s` com fallback léxico/pausa) e `split_topic_into_parts` (`<= 65s` único; `65–180s` em 2–3 partes de `45–58s` em fim de frase/pausa `>= 0.5s`; `> 180s` recortado para `180s` ao redor do pico).
**Where**: `clipes/topics.py`
**Depends on**: T2
**Reuses**: `Word.ends_sentence` de `clipes/models.py`
**Requirement**: PART-02, PART-03, PART-04

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [ ] `segment_topics` carrega `/opt/models/embed/onnx/model_quint8_avx2.onnx` no container e faz fallback limpo para pausas `>= 0.8s` se o arquivo ONNX não existir
- [ ] `split_topic_into_parts` garante que nenhuma parte de série exceda `58.0s` de conteúdo e que todo corte ocorra em `Word.ends_sentence` ou pausa `>= 0.5s`
- [ ] Suíte unitária criada para `topics` e gate passa: `docker compose run --rm clipes pytest -q`
- [ ] Test count: `>= 15` testes passam

**Tests**: unit
**Gate**: quick

---

### Phase 2: Snapping, ASS Rendering & Hardware Calibration (M1, M4)

### T4: [repo: clipes-lives] Adaptar clipes/snap.py para cortes por assunto e séries multi-parte

**What**: Integrar `segment_topics` e `split_topic_into_parts` em `clipes/snap.py` (`snap_topic_parts` e `select_clips`), dispensando a trava `PEAK_POS_MIN/MAX = 0.35/0.75` quando `edge_mode == "topic"`, preservando risada pós-fala (`_extend_past_reaction`) até o teto de `58.0s` por parte e mantendo todas as partes do mesmo `group_id` juntas no NMS sem truncar séries em `max_clips`.
**Where**: `clipes/snap.py`
**Depends on**: T3
**Reuses**: `_extend_past_reaction`, `_cuts_mid_word`, `_speech_start_candidates`, `_speech_end_candidates` em `clipes/snap.py`
**Requirement**: PART-03, PART-04

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [x] Candidato com `peak_ratio = 0.85` (punchline no fim da história) gera clipe válido com setup completo
- [x] Assunto de `120s` gera 2 ou 3 `SnappedClip` consecutivos (`part_index=1..N, part_total=N`) com `duration <= 58.0s` e `mid_word_cut_rate == 0.0`
- [x] Nenhuma série multi-parte sai truncada ao atingir `max_clips`
- [x] Gate passa: `docker compose run --rm clipes pytest -q`
- [x] Test count: `>= 18` testes passam

**Tests**: unit
**Gate**: quick

---

### T5: [repo: clipes-lives] Adicionar selo Parte k/N em clipes/render.py e atualizar CLI/eval

**What**: Atualizar `build_ass` em `clipes/render.py` para emitir `Style: PartBadge` (`y=490`) e `Dialogue` com `Parte {part_index}/{part_total}` quando `part_total > 1`, ajustar `evaluate_clips` (`clipes/eval.py`) para validar `d <= 58.0` em partes e aceitar `edge_mode == "topic"`, e expor `part_index`, `part_total` e `group_id` no manifesto `clips.json` (`clipes/cli.py`).
**Where**: `clipes/render.py`
**Depends on**: T4
**Reuses**: `build_ass` em `clipes/render.py`, `evaluate_clips` em `clipes/eval.py`
**Requirement**: PART-05

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [x] `build_ass` inclui `Style: PartBadge` e `Parte 1/2` quando `part_total == 2`, e omite quando `part_total == 1`
- [x] `clips.json` e `README.md` registram `part_index`, `part_total` e `group_id`
- [x] Gate passa: `docker compose run --rm clipes pytest -v`
- [x] Test count: `>= 20` testes passam

**Tests**: integration
**Gate**: full

---

### T6: [repo: clipes-lives] Executar medição M1 (WER pt-BR e xRT small vs large-v3-turbo no 5500U)

**What**: Criar teste de medição `tests/test_bench_m1_wer.py` que roda dentro do container sobre trecho real de 5 min de `UBv07VGeD24` (ou fixture de áudio pt-BR + referência corrigida pelo usuário), medindo WER, `xRT` e RSS de `small` e `large-v3-turbo`.
**Where**: `tests/test_bench_m1_wer.py`
**Depends on**: T5
**Reuses**: `clipes/asr.py`
**Requirement**: PART-06

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [ ] Medição executada via `docker compose run --rm clipes pytest tests/test_bench_m1_wer.py -v -s`
- [ ] Critérios numéricos verificados: `xRT_small <= 0.60`, `RSS_small <= 2.0 GB`, `RSS_turbo <= 3.5 GB`, e tabela comparativa de WER pt-BR (`small` vs `large-v3-turbo`) impressa e validada
- [ ] Gate passa: `docker compose run --rm clipes pytest -v`
- [ ] Test count: `>= 21` testes passam

**Tests**: integration
**Gate**: full

---

### T7: [repo: clipes-lives] Executar medição M4 (acerto de fronteiras do MiniLM ONNX em transcrição real)

**What**: Criar teste de medição `tests/test_bench_m4_topics.py` que avalia `segment_topics` com o modelo `/opt/models/embed/onnx/model_quint8_avx2.onnx` sobre uma transcrição real de live pt-BR com 3–5 mudanças de assunto marcadas.
**Where**: `tests/test_bench_m4_topics.py`
**Depends on**: T6
**Reuses**: `clipes/topics.py`
**Requirement**: PART-07

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [ ] Medição executada via `docker compose run --rm clipes pytest tests/test_bench_m4_topics.py -v -s`
- [ ] Critérios numéricos verificados: tempo de inferência `< 5.0s`, delta médio de similaridade de cosseno intra vs inter-assunto `>= 0.10`, RAM adicional `< 0.9 GB` e recall de fronteiras (tolerância `±1` frase ou `±3.0s`) `>= 0.75`
- [ ] Gate passa: `docker compose run --rm clipes pytest -v`
- [ ] Test count: `>= 22` testes passam

**Tests**: integration
**Gate**: full

**Resultado (2026-11-17):** medição rodada em `bench/m4_topics.py` (container, host Intel i5-10500, não o Ryzen 5 5500U), 17 fronteiras rotuladas à mão. Recall 0,71 e delta de cosseno 0,082 ficam abaixo do critério. Detalhe: `pesquisa/MEDICOES-M3-M4.md`. As caixas acima continuam abertas.

**Resultado (2026-11-17):** medição rodada em `bench/m4_topics.py` (container, host Intel i5-10500, não o Ryzen 5 5500U), 17 fronteiras rotuladas à mão. Recall 0,71 e delta de cosseno 0,082 ficam abaixo do critério. Detalhe: `pesquisa/MEDICOES-M3-M4.md`. As caixas acima continuam abertas.

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
| T1: Estender dataclasses em `clipes/models.py` | 1 arquivo (`clipes/models.py`) | ✅ Granular |
| T2: Janela assimétrica em `clipes/candidates.py` | 1 função (`pick_candidate_peaks`) | ✅ Granular |
| T3: Segmentador e divisor em `clipes/topics.py` | 1 módulo novo coeso (`clipes/topics.py`) | ✅ Granular |
| T4: Snapping por assunto e partes em `clipes/snap.py` | 1 arquivo (`clipes/snap.py`) | ✅ Granular |
| T5: Selo `Parte k/N` no ASS e manifesto | `clipes/render.py` + propagação mínima | ✅ Granular |
| T6: Medição M1 (WER `small` vs `turbo`) | 1 arquivo de benchmark (`tests/test_bench_m1_wer.py`) | ✅ Granular |
| T7: Medição M4 (Fronteiras MiniLM real) | 1 arquivo de benchmark (`tests/test_bench_m4_topics.py`) | ✅ Granular |

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
| T1: Modelos de partes | Domain (`models.py`) | unit | unit | ✅ OK |
| T2: Janela assimétrica | Domain (`candidates.py`) | unit | unit | ✅ OK |
| T3: Segmentador `topics.py` | Domain (`topics.py`) | unit | unit | ✅ OK |
| T4: Snapping de partes | Domain (`snap.py`) | unit | unit | ✅ OK |
| T5: Renderização `PartBadge` | Render / CLI (`render.py`) | integration | integration | ✅ OK |
| T6: Medição M1 | Benchmark M1 | integration | integration | ✅ OK |
| T7: Medição M4 | Benchmark M4 | integration | integration | ✅ OK |
