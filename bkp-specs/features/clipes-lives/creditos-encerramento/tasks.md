# creditos-encerramento Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path. The skill is the source of truth for the full flow (per-task cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user - do not proceed without it.**

---

**Design**: `/home/romulo/Projetos/planos_tlc-spec-driven/clipes-lives/features/clipes-lives/creditos-encerramento/design.md`
**Status**: Approved

---

## Test Coverage Matrix

> Generated from codebase, project guidelines, and spec - confirm before Execute. Guidelines found: `README.md`, `pyproject.toml` (`docker compose run --rm clipes pytest`).

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| ---------- | ------------------ | -------------------- | ---------------- | ----------- |
| Modelos / Ingest / ASS (`models.py`, `ingest.py`, `render.py`) | unit | Todas as branches; 1:1 com ACs da spec; safe zones `MarginV=560`, estilos `Credit`/`Card` e fallback sem fala | `tests/test_render_eval.py` | `docker compose run --rm clipes pytest` |
| Detector P2 YuNet (`reframe.py`) | unit | Extração de quadros 1 fps, inferência ONNX e fallback para `None` | `tests/test_render_eval.py` | `docker compose run --rm clipes pytest` |
| Renderização FFmpeg + CLI (`render.py`, `cli.py`) | integration | Execução real do `ffmpeg` e verificação com `ffprobe` de duração `D + 1.2s ± 0.05s`, resolução `1080x1920` e áudio `aac` | `tests/test_render_eval.py` | `docker compose run --rm clipes pytest -v` |

## Gate Check Commands

> Generated from codebase - confirm before Execute.

| Gate Level | When to Use | Command |
| ---------- | ----------- | ------- |
| Quick | Após tasks com testes unitários | `docker compose run --rm clipes pytest -q` |
| Full | Após tasks de integração com `ffmpeg`/`ffprobe` | `docker compose run --rm clipes pytest -v` |
| Build | Após conclusão de fase | `docker compose build && docker compose run --rm clipes pytest -v` |

---

## Execution Plan

Phases are ordered and run sequentially - each phase completes before the next begins, and tasks within a phase execute in order. Todas as 6 tasks compõem o **Lote Série 2 (`creditos-encerramento`)**, executando imediatamente após a conclusão de `partes-por-assunto`.

**Fase 1 — Identificação do Canal, Safe Zones ASS e Cadeia FFmpeg de Encerramento**

```
T1 -> T2 -> T3
```

**Fase 2 — Reenquadramento YuNet (P2), Flags CLI e Validação FFprobe**

```
T3 -> T4 -> T5 -> T6
```

---

## Task Breakdown

### Phase 1: Channel Credit, ASS Safe Zones & FFmpeg Outro Filterchain

### T1: [repo: clipes-lives] Adicionar campo channel em IngestResult e extrair crédito do canal em clipes/ingest.py

**What**: Adicionar `channel: str = ""` em `IngestResult` (`clipes/models.py`) e implementar `format_channel_credit(channel, url, extractor)` em `clipes/ingest.py`, populando `IngestResult.channel` e `meta.json` com `twitch.tv/<canal>` (Twitch) ou `@<canal>` (YouTube).
**Where**: `clipes/ingest.py`
**Depends on**: None
**Reuses**: `IngestResult` em `clipes/models.py`, `ingest` em `clipes/ingest.py`
**Requirement**: CRED-02

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [x] `format_channel_credit` formata `twitch.tv/gaules` para URLs Twitch e `@podpah` para YouTube
- [x] `ingest` e `ingest_local` preenchem `IngestResult.channel`
- [x] Gate passa: `docker compose run --rm clipes pytest -q`
- [x] Test count: `>= 12` testes passam

**Tests**: unit
**Gate**: quick

---

### T2: [repo: clipes-lives] Atualizar Safe Zones (MarginV=560, MarginL=120, MarginR=200) e eventos Credit/Card em clipes/render.py

**What**: Atualizar `build_ass` em `clipes/render.py` com as constantes `SAFE_MARGIN_L=120, SAFE_MARGIN_R=200, SAFE_MARGIN_V=560, OUTRO_DURATION=1.2`, adicionando `Style: Credit` (`0..4s` forte com `\fad(300,400)` + `4s..D` marca d'água `\alpha&H99&` em `\pos(120,600)`) e `Style: Card` (`[D, D+1.2]` em `\pos(540,960)\fad(250,0)` com `"Parte {k+1} →"` ou `"Live completa: {channel_credit}"`).
**Where**: `clipes/render.py`
**Depends on**: T1
**Reuses**: `build_ass`, `_ass_time`, `_ass_escape` em `clipes/render.py`
**Requirement**: CRED-01, CRED-02, CRED-04

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [x] `Style: Cap` em `build_ass` usa `120,200,560,1`
- [x] Eventos `Credit` (`\pos(120,600)`) e `Card` (`\pos(540,960)`) são gravados no `.ass` mesmo quando `words == []`
- [x] Parte intermediária (`part_index=1, part_total=2`) gera cartão `"Parte 2 →"` e parte final/única gera `"Live completa: ..."`
- [x] Gate passa: `docker compose run --rm clipes pytest -q`
- [x] Test count: `>= 15` testes passam

**Tests**: unit
**Gate**: quick

---

### T3: [repo: clipes-lives] Implementar encerramento tpad clone 1.2s e cadeia loudnorm->afade->apad em clipes/render.py

**What**: Atualizar `render_clip` em `clipes/render.py` para clonar o último quadro por `1.2s` (`tpad=stop_mode=clone:stop_duration=1.2`) antes de `subtitles`, ordenar o filtro de áudio como `loudnorm=I=-14:TP=-1.5:LRA=11,afade=t=out:st={max(0.0, D-0.5):.3f}:d=0.5,apad=pad_dur=1.2` e passar `-t {D + 1.2:.3f}` na saída do `ffmpeg`.
**Where**: `clipes/render.py`
**Depends on**: T2
**Reuses**: `render_clip` em `clipes/render.py`
**Requirement**: CRED-03

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [x] `render_clip` aplica `tpad=stop_mode=clone:stop_duration=1.2` sem fade para preto
- [x] Filtro `-af` mantém `loudnorm` antes de `afade` e `apad`
- [x] Duração do arquivo MP4 gerado é `clip.duration + 1.2s ± 0.05s`
- [x] Gate passa: `docker compose run --rm clipes pytest -q`
- [x] Test count: `>= 17` testes passam

**Tests**: integration
**Gate**: full

---

### Phase 2: Optional YuNet Reframing (P2), CLI Flags & FFprobe Verification

### T4: [repo: clipes-lives] Criar módulo opcional de reenquadramento vertical com YuNet ONNX (P2) em clipes/reframe.py

**What**: Criar `clipes/reframe.py` com `detect_speaker_crop(src_path, start, duration, yunet_onnx)` amostrando `1 fps` em `640x360` via `ffmpeg`, executando `/opt/models/yunet/face_detection_yunet_2023mar.onnx` no `onnxruntime` e retornando a caixa mediana para crop vertical (ou `None` se rosto presente em `< 30%` dos quadros).
**Where**: `clipes/reframe.py`
**Depends on**: T3
**Reuses**: `CLIPES_YUNET_ONNX` do `Dockerfile`
**Requirement**: CRED-05

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [x] `detect_speaker_crop` roda sobre o modelo `/opt/models/yunet/face_detection_yunet_2023mar.onnx` no container e retorna `None` de forma segura quando não há rosto estável
- [x] `render_clip` aceita `reframe: bool = False` e usa o layout 16:9 borrado padrão quando `reframe=False` ou `crop is None`
- [x] Gate passa: `docker compose run --rm clipes pytest -q`
- [x] Test count: `>= 19` testes passam

**Tests**: unit
**Gate**: quick

---

### T5: [repo: clipes-lives] Expor flags --channel e --reframe em clipes/cli.py e propagar para render_clip

**What**: Adicionar `--channel` e `--reframe` em `build_parser()` (`clipes/cli.py`), propagar `channel_credit` e `reframe` para `render_clip` e registrar `channel_credit` e `rendered_duration` em `clips.json`.
**Where**: `clipes/cli.py`
**Depends on**: T4
**Reuses**: `build_parser`, `run_pipeline` em `clipes/cli.py`
**Requirement**: CRED-02, CRED-05

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [x] `--channel` sobrescreve o canal inferido quando informado na CLI e `--reframe` ativa o detector YuNet opcional
- [x] `clips.json` inclui `channel_credit` e `rendered_duration`
- [x] Gate passa: `docker compose run --rm clipes pytest -v`
- [x] Test count: `>= 20` testes passam

**Tests**: integration
**Gate**: full

---

### T6: [repo: clipes-lives] Expandir tests/test_render_eval.py com validação real de ffprobe e quadros ASS

**What**: Adicionar testes de integração em `tests/test_render_eval.py` que geram um vídeo de entrada sintético com `ffmpeg`, executam `render_clip` para parte intermediária (`Parte 1/2`) e parte final (`Parte 2/2`), e validam via `ffprobe` que a duração de saída é `D + 1.2s ± 0.05s`, resolução `1080x1920` e eventos ASS corretos.
**Where**: `tests/test_render_eval.py`
**Depends on**: T5
**Reuses**: `render_clip`, `build_ass` em `clipes/render.py`, `probe_duration` em `clipes/ingest.py`
**Requirement**: CRED-01, CRED-02, CRED-03, CRED-04

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [x] Teste executa `render_clip` dentro do container e confirma `abs(probe_duration(mp4) - (clip.duration + 1.2)) <= 0.05`
- [x] Verifica presença de `MarginV=560`, `\pos(120,600)`, `\alpha&H99&`, `"Parte 2 →"` e `"Live completa:"`
- [x] Gate passa: `docker compose run --rm clipes pytest -v`
- [x] Test count: `>= 22` testes passam

**Tests**: integration
**Gate**: full

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
| T1: Campo `channel` e `format_channel_credit` | `clipes/ingest.py` (+ 1 atributo em `models.py`) | ✅ Granular |
| T2: Safe zones + `Style: Credit` e `Style: Card` | 1 função (`build_ass` em `clipes/render.py`) | ✅ Granular |
| T3: `tpad 1.2s` + `loudnorm,afade,apad` | 1 função (`render_clip` em `clipes/render.py`) | ✅ Granular |
| T4: Reenquadramento YuNet P2 (`clipes/reframe.py`) | 1 módulo opcional curto (`clipes/reframe.py`) | ✅ Granular |
| T5: Flags `--channel` e `--reframe` no CLI | 1 arquivo (`clipes/cli.py`) | ✅ Granular |
| T6: Testes de integração `ffmpeg`/`ffprobe` | 1 arquivo de teste (`tests/test_render_eval.py`) | ✅ Granular |

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
| T1: Extração de canal | Ingest (`ingest.py`) | unit | unit | ✅ OK |
| T2: Safe zones e estilos ASS | Render ASS (`render.py`) | unit | unit | ✅ OK |
| T3: Filtro `ffmpeg` de encerramento | Render FFmpeg (`render.py`) | integration | integration | ✅ OK |
| T4: Detector YuNet P2 | Domain (`reframe.py`) | unit | unit | ✅ OK |
| T5: Flags CLI | CLI (`cli.py`) | integration | integration | ✅ OK |
| T6: Validação `ffprobe` | Integration (`test_render_eval.py`) | integration | integration | ✅ OK |
