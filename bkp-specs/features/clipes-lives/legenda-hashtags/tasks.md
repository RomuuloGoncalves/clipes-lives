# legenda-hashtags Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path. The skill is the source of truth for the full flow (per-task cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user - do not proceed without it.**

---

**Design**: `/home/romulo/Projetos/planos_tlc-spec-driven/clipes-lives/features/clipes-lives/legenda-hashtags/design.md`
**Status**: Approved

---

## Test Coverage Matrix

> Generated from codebase, project guidelines, and spec - confirm before Execute. Guidelines found: `README.md`, `pyproject.toml` (`docker compose run --rm clipes pytest`).

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| ---------- | ------------------ | -------------------- | ---------------- | ----------- |
| LLM client / validadores / banco de hashtags (`llm.py`, `asr.py`) | unit | Todas as branches; 1:1 com ACs da spec; anti-alucinação, blocklist `#fyp`, remoção de hífen e fallback | `tests/test_llm.py` | `docker compose run --rm clipes pytest` |
| Integração CLI e manifesto (`cli.py`) | integration | Campos `caption`, `hashtags`, `llm_score` em `clips.json` e flag `--no-llm` | `tests/test_llm.py` | `docker compose run --rm clipes pytest` |
| Medição no piso M3 (`Qwen3-1.7B` e `4B`) | integration | Execução real no container (Ryzen 5 5500U) validando `tok/s`, RSS, `s/clipe` e qualidade em 8 clipes | `tests/test_bench_m3_llm.py` | `docker compose run --rm clipes pytest -v -s` |

## Gate Check Commands

> Generated from codebase - confirm before Execute.

| Gate Level | When to Use | Command |
| ---------- | ----------- | ------- |
| Quick | Após tasks com testes unitários | `docker compose run --rm clipes pytest -q` |
| Full | Após tasks de integração ou benchmark M3 | `docker compose run --rm clipes pytest -v` |
| Build | Após conclusão de fase | `docker compose build && docker compose run --rm clipes pytest -v` |

---

## Execution Plan

Phases are ordered and run sequentially - each phase completes before the next begins, and tasks within a phase execute in order. As tasks T1–T4 formam o núcleo independente de `clipes/llm.py` (rodando em paralelo com `monitor-twitch` e com o início da série), e T5–T6 integram no `cli.py` e executam a medição M3.

**Fase 1 — Liberação de RAM do Whisper, Subprocesso llama-server e Validadores**

```
T1 -> T2 -> T3
```

**Fase 2 — Enriquecimento em Lote, Integração CLI e Medição M3**

```
T3 -> T4 -> T5 -> T6
```

---

## Task Breakdown

### Phase 1: Memory Isolation, llama-server Subprocess & Output Validators

### T1: [repo: clipes-lives] Liberar explicitamente o WhisperModel da RAM em clipes/asr.py e estender SnappedClip

**What**: Garantir `del model; gc.collect()` antes do `return` de `transcribe_candidates` em `clipes/asr.py` (e adicionar `caption: str = ""`, `hashtags: list[str] = field(default_factory=list)`, `llm_score: float | None = None`, `llm_reason: str = ""` em `SnappedClip`).
**Where**: `clipes/asr.py`
**Depends on**: None
**Reuses**: `transcribe_candidates` em `clipes/asr.py`
**Requirement**: LLM-01

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [x] `transcribe_candidates` executa `del model; gc.collect()` ao terminar a transcrição das janelas
- [x] Testes unitários em `tests/test_llm.py` verificam que o coletor de lixo é acionado após o ASR
- [x] Gate passa: `docker compose run --rm clipes pytest -q`
- [x] Test count: `>= 11` testes passam

**Tests**: unit
**Gate**: quick

---

### T2: [repo: clipes-lives] Implementar complete(prompt, schema) e gerenciador llama-server em clipes/llm.py

**What**: Criar `clipes/llm.py` com context manager `llama_server_session` (iniciando `/opt/llama/llama-server -m $CLIPES_LLM_GGUF -t $CLIPES_CPU_THREADS -c 2048 --reasoning-budget 0` uma vez por lote e encerrando no `finally`) e a função `complete(prompt: str, schema: dict) -> dict` usando ChatML + `/no_think`, `temperature=0` e `urllib.request`.
**Where**: `clipes/llm.py`
**Depends on**: T1
**Reuses**: Variáveis `CLIPES_LLM_GGUF` e `CLIPES_CPU_THREADS` do `Dockerfile`
**Requirement**: LLM-01

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [x] `complete(prompt, schema)` envia payload com `/no_think`, `temperature=0` e `response_format` JSON Schema e devolve `dict` validado
- [x] `llama_server_session` encerra o processo filho (`terminate`/`kill`) mesmo quando ocorre exceção
- [x] Gate passa: `docker compose run --rm clipes pytest -q`
- [x] Test count: `>= 14` testes passam

**Tests**: unit
**Gate**: quick

---

### T3: [repo: clipes-lives] Implementar validador anti-alucinação, banco de hashtags e formatador de legenda em clipes/llm.py

**What**: Adicionar em `clipes/llm.py`: `validate_no_hallucinated_entities(title, transcript, reference_text)` (rejeitando palavras capitalizadas fora do início da frase ausentes da transcrição/metadados), `NICHE_HASHTAGS`, `sanitize_hashtags(raw_tags, channel, title)` (3–5 tags `^#[a-zA-Z0-9_À-ÿ]+$`, sem hífens e sem `#fyp/#foryou/#viral/#reels`) e `format_three_line_caption`.
**Where**: `clipes/llm.py`
**Depends on**: T2
**Reuses**: `generate_title` de `clipes/render.py`
**Requirement**: LLM-02, LLM-03, LLM-04

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [x] Títulos com nomes próprios inventados (ex.: `"Roubaram a moto do Professor!"` quando `"professor"` não está na transcrição) são rejeitados e caem para `generate_title`
- [x] Hashtags como `#gta-rp`, `#fyp`, `#viral`, `#reels`, `#foryou` são limpas/bloqueadas, restando sempre `3` a `5` hashtags válidas
- [x] `format_three_line_caption` gera exatamente 3 linhas incluindo `Crédito: <canal> · Live completa: <url>` e `Parte k/N` quando `part_total > 1`
- [x] Gate passa: `docker compose run --rm clipes pytest -q`
- [x] Test count: `>= 19` testes passam

**Tests**: unit
**Gate**: quick

---

### Phase 2: Batch Enrichment, CLI Wiring & M3 Hardware Benchmark

### T4: [repo: clipes-lives] Implementar enrich_clips_with_llm e desempate relativo por nota em clipes/llm.py

**What**: Implementar `enrich_clips_with_llm(clips, words, ing, enabled=True)` em `clipes/llm.py` processando o lote inteiro em uma única sessão do `llama-server`, aplicando rubrica ancorada 0–10 e usando `llm_score` apenas como desempate secundário (sem descartar clipes).
**Where**: `clipes/llm.py`
**Depends on**: T3
**Reuses**: `generate_title` de `clipes/render.py`
**Requirement**: LLM-01, LLM-04

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [x] `enrich_clips_with_llm` preserva `len(clips)` intacto mesmo se `llm_score` for baixo (`< 5`) ou se o servidor falhar
- [x] Clipes com `score` de sinais empatados (diferença `< 0.02`) são desempatados por `llm_score` mantendo partes de um mesmo `group_id` em ordem `1..N`
- [x] Gate passa: `docker compose run --rm clipes pytest -q`
- [x] Test count: `>= 22` testes passam

**Tests**: unit
**Gate**: quick

---

### T5: [repo: clipes-lives] Integrar enrich_clips_with_llm e flag --no-llm em clipes/cli.py

**What**: Adicionar `--no-llm` em `build_parser()`, invocar `enrich_clips_with_llm` após `select_clips` (e antes de `render_clip`) em `clipes/cli.py`, e gravar `caption`, `hashtags`, `llm_score`, `llm_reason` e `timings["llm_s"]` em `clips.json` e `README.md`.
**Where**: `clipes/cli.py`
**Depends on**: T4
**Reuses**: `run_pipeline` e `_write_run_readme` em `clipes/cli.py`
**Requirement**: LLM-04

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [x] `clips.json` contém `caption`, `hashtags`, `llm_score` e `llm_reason` em cada clipe e `llm_s` em `timings`
- [x] Passar `--no-llm` preenche `caption` e `hashtags` deterministicamente em `< 0.05s` sem subir `llama-server`
- [ ] Gate passa: `docker compose run --rm clipes pytest -v` (110 passed, 1 failed em `tests/test_llm_integration.py::test_real_llama_server_enrich_clips_smoke` por timeout de 45s do `llama-server` em `llm.py`)
- [x] Test count: `>= 24` testes passam (110 passed sem `test_llm_integration.py`)

**Tests**: integration
**Gate**: full

---

### T6: [repo: clipes-lives] Executar medição M3 (Qwen3-1.7B e 4B no Ryzen 5 5500U em 8 clipes reais)

**What**: Criar e executar `tests/test_bench_m3_llm.py` no container (`docker compose run --rm clipes pytest tests/test_bench_m3_llm.py -v -s`) rodando `llama-bench -t 6` e `enrich_clips_with_llm` sobre as 8 transcrições reais de `UBv07VGeD24`.
**Where**: `tests/test_bench_m3_llm.py`
**Depends on**: T5
**Reuses**: `clipes/llm.py`
**Requirement**: LLM-05

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [ ] Medição executada no container no Ryzen 5 5500U para `Qwen3-1.7B-Q4_K_M` (e `Qwen3-4B` se presente em `/opt/models/llm`)
- [ ] Critérios numéricos verificados no 1.7B: `tg >= 10.0 tok/s`, RSS máximo `<= 2.5 GB`, tempo médio `<= 20.0s/clipe`, `8/8` JSONs válidos, `3..5` hashtags sem `#fyp`/hífen e `0` nomes próprios alucinados
- [ ] Gate passa: `docker compose run --rm clipes pytest -v`
- [ ] Test count: `>= 25` testes passam

**Tests**: integration
**Gate**: full

**Resultado (2026-11-17):** medição rodada em `bench/m3_llm.py` (container, host Intel i5-10500, não o Ryzen 5 5500U). Qwen3-4B ausente na imagem. RSS 2,73 GB e 7/8 títulos válidos ficam fora do critério. Detalhe: `pesquisa/MEDICOES-M3-M4.md`. As caixas acima continuam abertas.

**Resultado (2026-11-17):** medição rodada em `bench/m3_llm.py` (container, host Intel i5-10500, não o Ryzen 5 5500U). Qwen3-4B ausente na imagem. Números e o que ficou fora do critério: `pesquisa/MEDICOES-M3-M4.md`. As caixas acima continuam abertas porque o hardware e o RSS (2,73 GB) não fecham o critério.

---

## Phase Execution Map

```
Phase 1:  T1 -> T2 -> T3
Phase 2:  T3 -> T4 -> T5 -> T6
```

---

## Task Granularity Check

| Task | Scope | Status |
| ---- | ----- | ------ |
| T1: Liberar Whisper da RAM (`asr.py`) | 1 função (`transcribe_candidates`) | ✅ Granular |
| T2: Subprocesso `llama-server` + `complete()` | 1 função + context manager (`clipes/llm.py`) | ✅ Granular |
| T3: Validadores de título, hashtags e legenda | 3 funções puras (`clipes/llm.py`) | ✅ Granular |
| T4: `enrich_clips_with_llm` + desempate | 1 função de orquestração (`clipes/llm.py`) | ✅ Granular |
| T5: Integração `--no-llm` e manifesto (`cli.py`) | 1 arquivo (`clipes/cli.py`) | ✅ Granular |
| T6: Medição M3 (`Qwen3-1.7B`/`4B` no 5500U) | 1 arquivo de benchmark (`tests/test_bench_m3_llm.py`) | ✅ Granular |

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

---

## Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
| ---- | --------------------------- | --------------- | --------- | ------ |
| T1: Liberação de RAM Whisper | Domain (`asr.py`) | unit | unit | ✅ OK |
| T2: Cliente `llama-server` | Domain (`llm.py`) | unit | unit | ✅ OK |
| T3: Validadores e hashtags | Domain (`llm.py`) | unit | unit | ✅ OK |
| T4: Enriquecimento em lote | Domain (`llm.py`) | unit | unit | ✅ OK |
| T5: Integração CLI | CLI (`cli.py`) | integration | integration | ✅ OK |
| T6: Medição M3 | Benchmark M3 | integration | integration | ✅ OK |
