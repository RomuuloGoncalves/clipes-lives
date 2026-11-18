# monitor-twitch Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path. The skill is the source of truth for the full flow (per-task cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user - do not proceed without it.**

---

**Design**: `/home/romulo/Projetos/planos_tlc-spec-driven/clipes-lives/features/clipes-lives/monitor-twitch/design.md`
**Status**: Approved

---

## Test Coverage Matrix

> Generated from codebase, project guidelines, and spec - confirm before Execute. Guidelines found: `README.md`, `pyproject.toml` (`docker compose run --rm clipes pytest`).

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| ---------- | ------------------ | -------------------- | ---------------- | ----------- |
| Ingest local / Descoberta / Política / Detector IRC / Fila (`ingest.py`, `monitor.py`) | unit | Todas as branches; 1:1 com ACs da spec; filtro de bots, Shared Chat, warmup/cooldown e transições atômicas da fila | `tests/test_monitor.py` | `docker compose run --rm clipes pytest` |
| Worker pipeline + Buffer rolante (`monitor.py`) | integration | Concatenação de segmentos, execução de `ingest_local` e transição `pending -> processing -> done` | `tests/test_monitor.py` | `docker compose run --rm clipes pytest` |
| Medições no piso M5 e M6 | integration | Execução no container (Ryzen 5 5500U) validando limites de CPU, RAM, disco e estabilidade IRC | `tests/test_bench_m5_recorder.py`, `tests/test_bench_m6_irc.py` | `docker compose run --rm clipes pytest -v -s` |

## Gate Check Commands

> Generated from codebase - confirm before Execute.

| Gate Level | When to Use | Command |
| ---------- | ----------- | ------- |
| Quick | Após tasks com testes unitários | `docker compose run --rm clipes pytest -q` |
| Full | Após tasks de integração ou benchmark | `docker compose run --rm clipes pytest -v` |
| Build | Após alteração no `Dockerfile` ou conclusão de fase | `docker compose build && docker compose run --rm clipes pytest -v` |

---

## Execution Plan

Phases are ordered and run sequentially - each phase completes before the next begins, and tasks within a phase execute in order. Todas as 7 tasks compõem o **Lote Paralelo A (`monitor-twitch`)**, que pode rodar em paralelo com `legenda-hashtags` e com o início da série (`partes-por-assunto`).

**Fase 1 — Ingest Local, Descoberta, Política e Detector IRC**

```
T1 -> T2 -> T3
```

**Fase 2 — Buffer Rolante, Fila/Worker e Medições M5/M6**

```
T3 -> T4 -> T5 -> T6 -> T7
```

---

## Task Breakdown

### Phase 1: Local Ingest, Discovery & Relative Chat Spike Detection

### T1: [repo: clipes-lives] Instalar streamlink no Dockerfile e adicionar ingest_local em clipes/ingest.py

**What**: Adicionar `"streamlink>=6.7.0"` no `Dockerfile` e implementar `ingest_local(local_dir: Path, out_root: Path, enable_chat: bool = True) -> IngestResult` em `clipes/ingest.py` para ingerir `source.mkv`, `meta.json` e `chat.json` diretamente do disco sem chamar `yt-dlp`.
**Where**: `clipes/ingest.py`
**Depends on**: None
**Reuses**: `extract_wav`, `probe_duration`, `IngestResult` em `clipes/ingest.py`
**Requirement**: MON-05, MON-06

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [x] `docker compose build` instala `streamlink` dentro da imagem sem erros (streamlink 8.6.3 já na imagem; instalação no Dockerfile ignorada por instrução)
- [x] `ingest_local` extrai `audio.wav` de `source.mkv`, carrega `chat.json` (`[t_rel, texto, autor_idx]`) e devolve `IngestResult` com `video_path = source.mkv` sem realizar chamadas de rede
- [x] Testes em `tests/test_monitor.py` criados e gate passa: `docker compose run --rm clipes pytest -q`
- [x] Test count: `>= 11` testes passam

**Tests**: unit
**Gate**: build

---

### T2: [repo: clipes-lives] Implementar descoberta de canais (channels.txt + Helix opcional) e policy.json em clipes/monitor.py

**What**: Criar `clipes/monitor.py` com `load_policy` (`blocked`, `allowed`, `delay_h`) e `discover_channels` lendo `channels.txt` por padrão e consultando Helix `GET /helix/streams` somente quando `TWITCH_CLIENT_ID` e `TWITCH_CLIENT_SECRET` estiverem em `os.environ`.
**Where**: `clipes/monitor.py`
**Depends on**: T1
**Reuses**: `sanitize_id` de `clipes/ingest.py`
**Requirement**: MON-01, MON-02

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [x] Sem variáveis `TWITCH_CLIENT_ID`/`TWITCH_CLIENT_SECRET`, `discover_channels` lê `channels.txt` e filtra canais `blocked` de `policy.json`
- [x] Com credenciais mockadas no ambiente, `discover_channels` pagina Helix respeitando `limit <= 80`, deduplica `user_login` e nunca grava tokens em disco
- [x] Gate passa: `docker compose run --rm clipes pytest -q`
- [x] Test count: `>= 14` testes passam

**Tests**: unit
**Gate**: quick

---

### T3: [repo: clipes-lives] Implementar parser IRC justinfan e detector de pico relativo Poisson 10s em clipes/monitor.py

**What**: Adicionar `parse_irc_line` (filtrando `BOT_AUTHORS`, comandos `!` e duplicatas de Shared Chat via `source-room-id != room-id`), `ChannelSpikeDetector` (`expected = max(mediana_10s_180s_sem_15s, 0.4*media, 2.5)`, `z >= 3.5`, `count10 >= 6`, `autores_unicos10 >= 4`, `count10/expected >= 3.0`, warmup `60s`, cooldown `180s`) e o cliente `irc_chat_loop` (`<= 80` canais, `JOIN` a cada `>= 0.6s`).
**Where**: `clipes/monitor.py`
**Depends on**: T2
**Reuses**: `BOT_AUTHORS` de `clipes/ingest.py`, lógica Poisson de `clipes/signals.py:218`
**Requirement**: MON-03, MON-04

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [x] Mensagens de Shared Chat (`source-room-id != room-id`), bots e `!comandos` são ignoradas na contagem
- [x] Flood de 20 mensagens de apenas 1–3 autores em 10s não dispara pico (`autores_unicos10 < 4`)
- [x] Rajada legítima (`>= 6` msgs, `>= 4` autores, `z >= 3.5`, `ratio >= 3.0`) após `60s` de warmup dispara `SpikeEvent` e entra em cooldown por `180s`
- [x] Gate passa: `docker compose run --rm clipes pytest -q`
- [x] Test count: `>= 18` testes passam

**Tests**: unit
**Gate**: quick

---

### Phase 2: Rolling Buffer Recorder, Atomic Disk Queue & M5/M6 Benchmarks

### T4: [repo: clipes-lives] Implementar RollingRecorderPool (streamlink + ffmpeg -c copy, buffer 5 min) em clipes/monitor.py

**What**: Implementar `RollingRecorderPool` gerenciando até `3` canais quentes + `1` slot sob demanda com `streamlink --stdout <canal> 720p60,720p,480p,best | ffmpeg -c copy -f segment -segment_time 10 -reset_timestamps 1`, limpeza de segmentos `> 300s`, captura de pré-roll `180s` + pós-roll `60–120s`, concatenação `-c copy` para `source.mkv` e exportação de `chat.json` (`[t_rel, texto, autor_idx]`).
**Where**: `clipes/monitor.py`
**Depends on**: T3
**Reuses**: `_compact_chat_rows` de `clipes/ingest.py`
**Requirement**: MON-05

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [x] Pool nunca excede `4` subprocessos simultâneos (`3` quentes + `1` sob demanda)
- [x] Limpeza remove arquivos `.ts` mais antigos que `300s`
- [x] `freeze_and_concat` gera `source.mkv` válido via `ffmpeg -f concat -safe 0 -c copy` e `chat.json` alinhado ao início do vídeo concatenado
- [x] Gate passa: `docker compose run --rm clipes pytest -v`
- [x] Test count: `>= 21` testes passam

**Tests**: integration
**Gate**: full

---

### T5: [repo: clipes-lives] Implementar DiskQueue atômica e worker sequencial em clipes/monitor.py

**What**: Implementar `DiskQueue` (`OUT_DIR/queue/{pending,processing,done,rejected}/<job_id>/` usando `os.replace`, com `recover_orphans()`) e `run_worker_step` executando 1 job por vez via `ingest_local` + pipeline, calculando `ready_at` conforme `delay_h` de `policy.json` e ordenando jobs prontos por `score` sem publicar.
**Where**: `clipes/monitor.py`
**Depends on**: T4
**Reuses**: `ingest_local` em `clipes/ingest.py`, pipeline de `clipes/cli.py`
**Requirement**: MON-02, MON-06

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [x] Jobs órfãos em `processing/` retornam para `pending/` na inicialização
- [x] Worker processa 1 job completo, preenche `ready_at = created_at + delay_h * 3600` (ex.: `+86400s` para `gaules`) e move para `done/` (ou `rejected/` se `clips == []`)
- [x] Gate passa: `docker compose run --rm clipes pytest -v`
- [x] Test count: `>= 24` testes passam

**Tests**: integration
**Gate**: full

---

### T6: [repo: clipes-lives] Executar medição M5 (3 gravações streamlink -c copy simultâneas + worker no 5500U)

**What**: Criar e executar `tests/test_bench_m5_recorder.py` dentro do container no Ryzen 5 5500U medindo CPU, RAM RSS e uso de disco de 3 gravações `-c copy` simultâneas em paralelo com 1 execução do worker.
**Where**: `tests/test_bench_m5_recorder.py`
**Depends on**: T5
**Reuses**: `RollingRecorderPool` e `run_worker_step` em `clipes/monitor.py`
**Requirement**: MON-07

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [ ] Medição executada via `docker compose run --rm clipes pytest tests/test_bench_m5_recorder.py -v -s`
- [ ] Critérios numéricos atendidos: disco de buffer `<= 200 MB` por canal, CPU por gravador `-c copy` `< 25%` de 1 thread, e RSS total do container `<= 4.5 GB`
- [ ] Gate passa: `docker compose run --rm clipes pytest -v`
- [ ] Test count: `>= 25` testes passam

**Tests**: integration
**Gate**: full

---

### T7: [repo: clipes-lives] Executar medição M6 (IRC justinfan 50-80 canais por 1h e calibração de falsos picos)

**What**: Criar e executar `tests/test_bench_m6_irc.py` validando a estabilidade do cliente IRC anônimo e a taxa de disparo de `ChannelSpikeDetector` em 50–80 canais ao longo de 1h (ou replay acelerado de 1h de tráfego IRC multicanal).
**Where**: `tests/test_bench_m6_irc.py`
**Depends on**: T6
**Reuses**: `irc_chat_loop` e `ChannelSpikeDetector` em `clipes/monitor.py`
**Requirement**: MON-07

**Tools**:

- MCP: NONE
- Skill: `ponytail`

**Done when**:

- [ ] Medição executada via `docker compose run --rm clipes pytest tests/test_bench_m6_irc.py -v -s`
- [ ] Critérios numéricos atendidos: RSS do monitor IRC `< 100 MB`, taxa de JOIN respeita `<= 20` JOINs por `10s`, e `0` falsos picos em canais estacionários ou com flood de único autor
- [ ] Gate passa: `docker compose run --rm clipes pytest -v`
- [ ] Test count: `>= 26` testes passam

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
| T1: `streamlink` no Docker + `ingest_local` | `clipes/ingest.py` (+ 1 linha no `Dockerfile`) | ✅ Granular |
| T2: Descoberta e `policy.json` | 2 funções em `clipes/monitor.py` | ✅ Granular |
| T3: IRC `justinfan` + `ChannelSpikeDetector` | 1 classe + parser em `clipes/monitor.py` | ✅ Granular |
| T4: `RollingRecorderPool` (`streamlink \| ffmpeg`) | 1 classe em `clipes/monitor.py` | ✅ Granular |
| T5: `DiskQueue` + worker sequencial | 1 classe + função worker em `clipes/monitor.py` | ✅ Granular |
| T6: Medição M5 (3 gravações + worker) | 1 arquivo de benchmark (`tests/test_bench_m5_recorder.py`) | ✅ Granular |
| T7: Medição M6 (IRC 50–80 canais) | 1 arquivo de benchmark (`tests/test_bench_m6_irc.py`) | ✅ Granular |

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
| T1: `ingest_local` | Ingest (`ingest.py`) | unit | unit | ✅ OK |
| T2: Descoberta e política | Domain (`monitor.py`) | unit | unit | ✅ OK |
| T3: Detector IRC Poisson | Domain (`monitor.py`) | unit | unit | ✅ OK |
| T4: `RollingRecorderPool` | Integration (`monitor.py`) | integration | integration | ✅ OK |
| T5: `DiskQueue` + Worker | Integration (`monitor.py`) | integration | integration | ✅ OK |
| T6: Medição M5 | Benchmark M5 | integration | integration | ✅ OK |
| T7: Medição M6 | Benchmark M6 | integration | integration | ✅ OK |
