# Onda B Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path. The skill is the source of truth for the full flow (per-task cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user - do not proceed without it.**

Regras do projeto (valem para toda task): nunca acessar banco (nenhuma conexão a URI real; testes com `mongomock`/fakes em memória; queries reais são escritas e entregues ao usuário); nada instalado no host (tudo no contêiner); nenhuma chamada de rede real em teste; nenhum segredo (URI, token, senha, chave, hostname) em arquivo versionado, log, spec ou commit; commit atômico local só no repo `clipes-lives`, sem push, sem deploy, sem publicar; nada de benchmark pesado (PC de piso desliga sob 6 threads); antes de `docker compose build` conferir `df -h /` com >= 6 GB livres e rodar `docker image prune -f` depois (não apagar volumes).

---

**Design**: `features/clipes-lives/fila-distribuida/issues/onda-b-fila-mongo-dashboard/design.md`
**Status**: Draft

---

## Test Coverage Matrix

> Generated from codebase, project guidelines, and spec - confirm before Execute. Guidelines found: `pesquisa/REGRAS-DEV.md` (testes no contêiner, fakes, sem rede), `pyproject.toml` (sem limiar de cobertura). Sem linter configurado.

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| ---------- | ------------------ | -------------------- | ---------------- | ----------- |
| Scripts sh (`vigia-temp`, `clipar`) | unit (pytest roda o sh com sysfs/PATH falsos) | Todos os ramos de TEMP-01/02 e CAP-01 | `tests/test_vigia_temp.py`, `tests/test_clipar_perfil.py` | quick |
| Módulos puros novos (`capacidade`, `janelas`) | unit | Todos os ramos; 1:1 com ACs; edge cases | `tests/test_capacidade.py`, `tests/test_janelas.py` | quick |
| Coordenação (`coord.py`) | integration (mongomock em memória) | Corrida A/B, lease vencida, fencing, idempotência, DuplicateKey, máscara da URI | `tests/test_coord.py` | quick |
| Painel (`painel/api/*.py`) | integration (handler em porta efêmera + mongomock) | 200/401/500/400/409/202, online/offline, nenhum segredo no JSON | `tests/test_painel.py` | quick (montando `painel/`) |
| Perfil (`perfil.py`) | unit | razão, `max_lives`, invalidação por `cpus` | `tests/test_perfil.py` | quick |
| Página estática e `vercel.json` | none | build gate (grep sem `innerHTML`, JSON válido) | - | build |
| Monitor/CLI (`monitor.py`, `cli.py`) | unit (fakes de pool, coord, relógio) | Caminhos novos + regressão da suíte existente | `tests/test_monitor_*.py`, `tests/test_cli_*.py` | full |
| ASR/LLM/tópicos (troca de default de threads) | unit | Default vem de `cpu_threads()` | `tests/test_capacidade.py` | quick |
| Infra (`Dockerfile`, `entrypoint.sh`, `docker-compose.yml`, README) | none | build gate only | - | build |

## Gate Check Commands

> Generated from codebase - confirm before Execute. Rodar na raiz da worktree.

| Gate Level | When to Use | Command |
| ---------- | ----------- | ------- |
| Quick | Após tasks com testes unitários | `docker compose run --rm -v "$PWD/clipes:/app/clipes" -v "$PWD/tests:/app/tests" test pytest -q tests/<arquivo_da_task>` |
| Full | Fim de cada fase | `docker compose run --rm -v "$PWD/clipes:/app/clipes" -v "$PWD/tests:/app/tests" test pytest -q` |
| Build | Tasks de infra (Dockerfile, compose, scripts sh) e fim da onda | `df -h / && docker compose config -q && sh -n clipar && sh -n vigia-temp && docker compose build && docker compose run --rm test sh -c "python -m compileall -q clipes && pytest -q"` (o `docker compose build` só quando o Dockerfile ou o `entrypoint.sh` mudou) |

---

## Execution Plan

Fases em sequência. Dentro de uma fase, cadeias sem seta podem rodar em paralelo SÓ porque os arquivos são disjuntos. Toda task em `cli.py`, `render.py`, `llm.py` ou `monitor.py` é serial (aqui e entre ondas: a Onda B só toca `llm.py`/`monitor.py`/`cli.py` depois de a Onda A fechar).

---

## Task Breakdown

### Phase 1: Fundação (paralelo: arquivos disjuntos)

Cadeias sem seta podem rodar em paralelo (arquivos disjuntos). O vigia vem primeiro em prioridade: segurança física.

```
T1
T2
T3
T4 → T5
```

### T1: [repo: clipes-lives] Vigia térmico lê hwmon e grava .temp

**What**: Trocar a leitura de `thermal_zone` por hwmon filtrado por nome (`k10temp`, `zenpower`, `coretemp`, `cpu_thermal`) sob `${SYSFS_ROOT:-/sys}`, fallback `thermal_zone` legível, `sem-sensor` em vez de 0, gravar `$OUT_DIR/.temp`, e modo `--check` (1 leitura, imprime e sai).
**Where**: `vigia-temp`
**Depends on**: None
**Reuses**: laço atual de pausa/retomada em `vigia-temp:7-17`; esboço do relatório B seção 2.4
**Requirement**: TEMP-01
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] pytest roda o script com `SYSFS_ROOT` falso e `docker` falso no `PATH`: 86 °C → `pause`; 74 °C após pausa → `unpause`
- [x] Só `nvme`/`BAT0` e sem `thermal_zone` → `.temp` = `sem-sensor`, nenhuma linha `0C` no log
- [x] `--check` imprime `78` ou `sem-sensor` e sai 0
- [x] Gate check passes: quick em `tests/test_vigia_temp.py`

**Tests**: unit
**Gate**: quick
**Commit**: `fix(vigia): le temperatura do hwmon da CPU e grava .temp`

---

### T2: [repo: clipes-lives] Módulo de capacidade do PC

**What**: Criar `cpu_threads()`, `capacity(out_dir)` e `worker_id(out_dir)` com raízes `/sys` e `/proc` injetáveis e `run` injetável para `nvidia-smi`.
**Where**: `clipes/capacidade.py` (novo)
**Depends on**: None
**Reuses**: stdlib (`shutil.disk_usage`, `uuid`, `subprocess`)
**Requirement**: CAP-02, CAP-03
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] `cpu.max="300000 100000"` → 3; `max 100000` → `os.cpu_count()`; `CLIPES_CPU_THREADS=2` → 2
- [x] `.temp` com `sem-sensor` ou ausente → `temp_c=None`; `nvidia-smi` ausente → `gpu=None`; com GPU falsa → `score` dobra
- [x] `worker_id` cria `w-<12 hex>` 1x e reaproveita
- [x] Gate check passes: quick em `tests/test_capacidade.py`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(capacidade): detecta threads, RAM, disco, temperatura e GPU do PC`

---

### T3: [repo: clipes-lives] Janelas alinhadas, região dona e emitidos

**What**: Funções puras `window_end`, `owns` e `filter_emitted` (sobreposição >= 50% com intervalo já emitido é descartada; aceitos gravados em `emitted.json` com escrita atômica).
**Where**: `clipes/janelas.py` (novo)
**Depends on**: None
**Reuses**: fórmula do relatório B seção 4.4
**Requirement**: LIVE-01
**Implementador**: Claude Sonnet 5.5 | **Revisor**: Gemini 4 Hydrogen

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] Janelas sintéticas com picos nas bordas: cada pico aceito exatamente 1 vez
- [x] Última janela dona de `[e − STEP − pad, e]`
- [x] `emitted.json` corrompido → tratado como vazio e reescrito, com log
- [x] Gate check passes: quick em `tests/test_janelas.py`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(live): janelas alinhadas com regiao dona e dedup por tempo absoluto`

---

### T4: [repo: clipes-lives] Threads do OpenMP vindas da cota

**What**: No `entrypoint.sh`, exportar `OMP_NUM_THREADS` e `OPENBLAS_NUM_THREADS` = `CLIPES_CPU_THREADS` ou `floor(quota/period)` de `/sys/fs/cgroup/cpu.max`, mínimo 1.
**Where**: `entrypoint.sh`
**Depends on**: None
**Reuses**: script atual
**Requirement**: CAP-02
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] `sh -n entrypoint.sh` ok; com `cpus: 3` o contêiner mostra `OMP_NUM_THREADS=3`
- [x] Gate check passes: build

**Tests**: none
**Gate**: build
**Commit**: `fix(docker): threads do OpenMP seguem a cota de CPU do conteiner`

---

### T5: [repo: clipes-lives] Dependências pymongo e mongomock na imagem

**What**: Camada final `RUN pip install "pymongo>=4.8" "mongomock>=4.3"` (depois das camadas de modelo) e uma reconstrução.
**Where**: `Dockerfile`
**Depends on**: T4
**Reuses**: camada de dependências em `Dockerfile:59`
**Requirement**: FILA-01
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] `df -h /` >= 6 GB antes do build; `docker image prune -f` depois
- [x] `docker compose run --rm test python -c 'import pymongo, mongomock'` ok
- [x] Gate check passes: build

**Tests**: none
**Gate**: build
**Commit**: `build(docker): adiciona pymongo e mongomock`

---

### Phase 2: Coordenação no Mongo (coord.py, serial)

Arquivo novo, disjunto do núcleo: pode correr junto com a Fase 3 em outro worker.

```
T6 → T7 → T8 → T9
```

### T6: [repo: clipes-lives] Lives com lease de dono

**What**: Classe `Coord(db, worker_id, now, lease_s)` com `ensure_indexes`, `ensure_live`, `claim_live`, `renew`, `release`, e `mask_uri` no módulo.
**Where**: `clipes/coord.py` (novo)
**Depends on**: None
**Reuses**: operadores do relatório B seção 1.4
**Requirement**: FILA-01, FILA-03
**Implementador**: Claude Sonnet 5.5 | **Revisor**: Gemini 4 Hydrogen

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] 1º teste confirma que o mongomock aceita `$or` + `$lt` no `find_one_and_update` (senão: fake próprio mínimo, registrado como desvio)
- [x] A reivindica, B recebe `False`; relógio +91 s sem renovar → B reivindica; `renew` de A → `False`
- [x] `ensure_live` concorrente com `DuplicateKeyError` não levanta
- [x] `mask_uri('mongodb+srv://u:p@h/x')` == `mongodb+srv://***@***`
- [x] Gate check passes: quick em `tests/test_coord.py`

**Tests**: integration
**Gate**: quick
**Commit**: `feat(coord): lease de dono por live no MongoDB`

---

### T7: [repo: clipes-lives] Jobs idempotentes por janela e janelas perdidas

**What**: `add_job` (insert com `_id` determinístico, ignora duplicata), `start_job` (`attempts`, `failed` com >= 3), `finish_job` (filtro `{_id, owner, status: processing}`) e `mark_lost(live_id, antigo_owner)` chamado quando a live é reivindicada de um dono vencido.
**Where**: `clipes/coord.py`
**Depends on**: T6
**Reuses**: `Coord`
**Requirement**: FILA-02, FILA-01
**Implementador**: Claude Sonnet 5.5 | **Revisor**: Gemini 4 Hydrogen

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] Concluir 2x deixa 1 documento `done` e devolve `False` na 2ª; outro `owner` não conclui; 3ª tentativa → `failed`
- [x] Reivindicar live de dono vencido marca `lost` os jobs pendentes dele; `add_job` com `_id` existente não recria
- [x] Gate check passes: quick em `tests/test_coord.py`

**Tests**: integration
**Gate**: quick
**Commit**: `feat(coord): jobs por janela idempotentes e janelas perdidas`

---

### T8: [repo: clipes-lives] Heartbeat do PC

**What**: `beat(alias, capacity, job_atual, max_lives, razao)` com `$set` + `$push` em `history` com `$slice: -180`, upsert por `worker_id`.
**Where**: `clipes/coord.py`
**Depends on**: T7
**Reuses**: `Coord`
**Requirement**: PAINEL-01, PERF-02
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] 181 batidas deixam `len(history)==180`
- [x] Documento nunca contém hostname nem URI
- [x] Gate check passes: quick em `tests/test_coord.py`

**Tests**: integration
**Gate**: quick
**Commit**: `feat(coord): heartbeat com metricas e historico curto do PC`

---

### T9: [repo: clipes-lives] Leitura e baixa de comandos

**What**: `pending_command()` (só do próprio `worker_id`, mais antigo) e `mark_command(id, status)`; ação fora da lista branca → `rejeitado`.
**Where**: `clipes/coord.py`
**Depends on**: T8
**Reuses**: `Coord`
**Requirement**: CMD-02, CMD-01
**Implementador**: Claude Sonnet 5.5 | **Revisor**: Gemini 4 Hydrogen

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] Comando de outro `worker_id` não é lido
- [x] `acao: "apagar"` → `rejeitado`, nunca devolvido
- [x] Gate check passes: quick em `tests/test_coord.py`

**Tests**: integration
**Gate**: quick
**Commit**: `feat(coord): comandos do painel por PC com lista branca`

---

### Phase 3: Limites automáticos (arquivos disjuntos; llm.py em série com as outras ondas)

Paralelo permitido dentro da fase: arquivos diferentes. `llm.py` só depois de a Onda A fechar.

```
T10
T11
T12
T13
T14
```

### T10: [repo: clipes-lives] ASR usa cpu_threads

**What**: Trocar o default `6` de `clipes/asr.py:64,174` por `cpu_threads()`.
**Where**: `clipes/asr.py`
**Depends on**: None
**Reuses**: `cpu_threads`
**Requirement**: CAP-02
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] `WhisperModel` falso recebe `cpu_threads()` sem `CLIPES_CPU_THREADS`
- [x] Gate check passes: quick em `tests/test_capacidade.py`

**Tests**: unit
**Gate**: quick
**Commit**: `fix(asr): threads padrao seguem a cota do conteiner`

---

### T11: [repo: clipes-lives] Embeddings usam cpu_threads

**What**: Trocar o default `6` de `clipes/topics.py:86` por `cpu_threads()`.
**Where**: `clipes/topics.py`
**Depends on**: None
**Reuses**: `cpu_threads`
**Requirement**: CAP-02
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] Sessão ONNX falsa recebe `intra_op_num_threads = cpu_threads()`
- [x] Gate check passes: quick em `tests/test_topics.py`

**Tests**: unit
**Gate**: quick
**Commit**: `fix(topics): threads padrao seguem a cota do conteiner`

---

### T12: [repo: clipes-lives] LLM local usa cpu_threads

**What**: Trocar o default `6` de `clipes/llm.py:228` por `cpu_threads()`.
**Where**: `clipes/llm.py`
**Depends on**: None
**Reuses**: `cpu_threads`
**Requirement**: CAP-02
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] Comando do `llama-server` falso tem `-t <cpu_threads()>`
- [x] Gate check passes: quick em `tests/test_llm.py`

**Tests**: unit
**Gate**: quick
**Commit**: `fix(llm): threads padrao do llama-server seguem a cota`

---

### T13: [repo: clipes-lives] Perfil automático, aviso de sensor e reinício no clipar

**What**: Sem `CLIPES_CPUS`: `nproc // 4` com bateria ou `MemAvailable` < 4 GB, senão `nproc // 2`; sem `CLIPES_MEM`: menor entre 8 GB e 60% de `MemAvailable`; `vigia-temp --check` = `sem-sensor` → só aviso; código de saída 75 → `sleep ${CLIPES_RESTART_S:-60}` e repete o `docker compose run`. Raízes `/proc` e `/sys` via env para teste.
**Where**: `clipar`
**Depends on**: None
**Reuses**: `vigia-temp --check`
**Requirement**: CAP-01, TEMP-02, CMD-02
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] pytest roda `clipar` com `docker` falso e `/proc`/`/sys` falsos: 12 threads + bateria → `CLIPES_CPUS=3`; 16 sem bateria e 12 GB livres → 8; `CLIPES_CPUS=5` definido → 5
- [x] `sem-sensor` imprime o aviso e não muda limites
- [x] `docker` falso que sai 75 e depois 0 → 2 execuções; sai 1 → 1 execução
- [x] Gate check passes: quick em `tests/test_clipar.py`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(clipar): perfil automatico por PC e reinicio apos liberar recursos`

---

### T14: [repo: clipes-lives] Compose com memória e variáveis da fila

**What**: `mem_limit: ${CLIPES_MEM:-8g}` nos dois serviços e repasse sem valor de `CLIPES_MONGO_URI`, `CLIPES_ALIAS`, `CLIPES_LEASE_S`, `CLIPES_BEAT_S`, `CLIPES_MIN_FREE_GB`.
**Where**: `docker-compose.yml`
**Depends on**: None
**Reuses**: blocos atuais
**Requirement**: CAP-01, FILA-03
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] `docker compose config` sem nenhum valor de segredo no arquivo
- [x] Gate check passes: build

**Tests**: none
**Gate**: build
**Commit**: `chore(docker): memoria automatica e variaveis da fila no compose`

---

### Phase 4: Limite de lives medido (serial)



```
T15 → T16
```

### T15: [repo: clipes-lives] Medição da razão e max_lives

**What**: `medir(job_dir, pipeline)` grava `OUT_DIR/.perfil.json`; `max_lives(out_dir, cpus)` = `max(1, floor(0,6/razão))`, 1 sem arquivo ou com `cpus` diferente.
**Where**: `clipes/perfil.py` (novo)
**Depends on**: None
**Reuses**: `run_local_pipeline` (`clipes/monitor.py:721`) injetado
**Requirement**: PERF-01, PERF-02
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] Pipeline falso 100 s / 300 s → `max_lives=1`; 30 s → 6; sem arquivo → 1; `cpus` mudou → 1
- [x] Gate check passes: quick em `tests/test_perfil.py`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(perfil): mede razao de processamento e limite de lives por PC`

---

### T16: [repo: clipes-lives] Subcomando bench-live

**What**: Rotear `python -m clipes bench-live <job_dir>` para `perfil.medir` (aceita só pasta com `source.mkv` >= 300 s).
**Where**: `clipes/__main__.py`
**Depends on**: T15
**Reuses**: roteamento de `monitor` em `clipes/__main__.py:7`
**Requirement**: PERF-01
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] Pasta com fonte de 200 s → sai 2 com mensagem
- [x] Gate check passes: quick em `tests/test_perfil.py`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(cli): subcomando bench-live`

---

### Phase 5: Painel para a Vercel (serial)

Gate desta fase monta também `-v "$PWD/painel:/app/painel"` no comando do pytest.

```
T17 → T18 → T19 → T20 → T21
```

### T17: [repo: clipes-lives] Núcleo do painel

**What**: `autorizado(header, senha)`, `status(db, now)` (online por batida, lives com dono, jobs 24 h por status) e `criar_comando(db, worker_id, confirmar, now)` (400 sem `LIBERAR`, 409 se já pendente, 404 worker inexistente, 202 ok).
**Where**: `painel/api/_nucleo.py` (novo)
**Depends on**: None
**Reuses**: `secrets.compare_digest`; documentos de `coord.py`
**Requirement**: PAINEL-02, CMD-01
**Implementador**: Claude Sonnet 5.5 | **Revisor**: Gemini 4 Hydrogen

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] Com mongomock: 61 s sem batida → `online=false`; comando sem confirmação 400, ok 202, repetido 409
- [x] Senha errada ou ausente → `autorizado` falso; nenhum campo de URI/hostname no JSON
- [x] Gate check passes: quick em `tests/test_painel.py`

**Tests**: integration
**Gate**: quick
**Commit**: `feat(painel): status dos PCs e comando liberar com Basic Auth`

---

### T18: [repo: clipes-lives] Função /api/status

**What**: `class handler(BaseHTTPRequestHandler)` que lê `PAINEL_SENHA`/`PAINEL_MONGO_URI`, responde 401/500/200 via `_nucleo` (formato de função Python da Vercel conferido na doc oficial).
**Where**: `painel/api/status.py` (novo)
**Depends on**: T17
**Reuses**: `_nucleo`
**Requirement**: PAINEL-02
**Implementador**: Claude Sonnet 5.5 | **Revisor**: Gemini 4 Hydrogen

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] Handler exercitado com servidor local em porta efêmera e client mongomock injetado: 401 sem auth + `WWW-Authenticate`, 500 `painel nao configurado` sem env, 200 com auth
- [x] Gate check passes: quick em `tests/test_painel.py`

**Tests**: integration
**Gate**: quick
**Commit**: `feat(painel): funcao de status para a Vercel`

---

### T19: [repo: clipes-lives] Função /api/comando

**What**: Handler `POST` que chama `criar_comando` e devolve o código do núcleo.
**Where**: `painel/api/comando.py` (novo)
**Depends on**: T18
**Reuses**: `_nucleo`
**Requirement**: CMD-01
**Implementador**: Claude Sonnet 5.5 | **Revisor**: Gemini 4 Hydrogen

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] `GET` → 405; `POST` sem auth → 401; com `confirmar=LIBERAR` → 202
- [x] Gate check passes: quick em `tests/test_painel.py`

**Tests**: integration
**Gate**: quick
**Commit**: `feat(painel): funcao de comando liberar recursos`

---

### T20: [repo: clipes-lives] Página do painel

**What**: `index.html` sem framework: tabela de PCs (apelido, online, CPU, RAM, disco, temperatura com `n/d` destacado, GPU, job, `max_lives`), `fetch` a cada 10 s, texto sempre por `textContent`, botão "liberar recursos" com `confirm()` + digitar `LIBERAR`.
**Where**: `painel/index.html` (novo)
**Depends on**: T19
**Reuses**: `/api/status`, `/api/comando`
**Requirement**: PAINEL-02, CMD-01
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] Nenhum `innerHTML` com dado do banco (grep no gate)
- [x] Gate check passes: build

**Tests**: none
**Gate**: build
**Commit**: `feat(painel): pagina de status com botao liberar recursos`

---

### T21: [repo: clipes-lives] Configuração da Vercel

**What**: `painel/requirements.txt` (`pymongo`) e `painel/vercel.json` mínimo; nenhum segredo (só nomes de env documentados).
**Where**: `painel/vercel.json` (novo)
**Depends on**: T20
**Reuses**: doc oficial da Vercel para Python
**Requirement**: PAINEL-02
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] JSON válido; sem deploy (o usuário publica)
- [x] Gate check passes: build

**Tests**: none
**Gate**: build
**Commit**: `chore(painel): configuracao de deploy na Vercel`

---

### Phase 6: Integração no monitor (SERIAL: monitor.py)



```
T22 → T23 → T24
```

### T22: [repo: clipes-lives] Backend Mongo ou disco no monitor

**What**: Com `CLIPES_MONGO_URI`: criar `Coord` e ligar `add_job`/`start_job`/`finish_job` ao redor de `run_worker_step`; sem URI ou com Mongo fora: `DiskQueue` + `flock` atuais e reconexão a cada 60 s; toda mensagem de erro passa por `mask_uri`.
**Where**: `clipes/monitor.py`
**Depends on**: None
**Reuses**: `run_worker_step`, `_lock_worker`, `Coord`
**Requirement**: FILA-02, FILA-03
**Implementador**: Claude Sonnet 5.5 | **Revisor**: Gemini 4 Hydrogen

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] Sem URI: `pymongo` não importado (`sys.modules`)
- [x] Coord falso que levanta `ServerSelectionTimeoutError` → job segue local, log sem URI
- [x] Gate check passes: full

**Tests**: unit
**Gate**: full
**Commit**: `feat(monitor): coordenacao opcional pelo MongoDB com queda para fila local`

---

### T23: [repo: clipes-lives] Reivindicar a live antes de gravar

**What**: Guardar `stream_id` do Helix (`id`) ou do yt-dlp; `ensure_live` + espera `30 s / score` + `claim_live` (só se lives próprias < `max_lives`) antes de `start_on_demand`; sem claim, não gravar.
**Where**: `clipes/monitor.py`
**Depends on**: T22
**Reuses**: `_helix_logins`, `capture_job`, `perfil.max_lives`
**Requirement**: FILA-01, PERF-02
**Implementador**: Claude Sonnet 5.5 | **Revisor**: Gemini 4 Hydrogen

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] Coord falso que nega claim → gravador falso nunca é iniciado; com `max_lives=1` e 1 live própria não reivindica outra
- [x] Espera proporcional a `1/score` com relógio falso
- [x] Gate check passes: full

**Tests**: unit
**Gate**: full
**Commit**: `feat(monitor): um PC dono por live respeitando o limite medido`

---

### T24: [repo: clipes-lives] Heartbeat, lease e comando liberar no monitor

**What**: Tarefa a cada `CLIPES_BEAT_S`: `beat(...)`, `renew` de cada live própria (falso → parar gravador e log `[fila] lease perdida`) e `pending_command()`; `liberar` → `executando`, `pool.stop_all()`, encerrar filhos, `feito`, log `[comando] liberar recursos`, sair 75.
**Where**: `clipes/monitor.py`
**Depends on**: T23
**Reuses**: `run_monitor`, `Coord`
**Requirement**: PAINEL-01, FILA-01, CMD-02
**Implementador**: Claude Sonnet 5.5 | **Revisor**: Gemini 4 Hydrogen

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] Relógio falso: 3 ciclos → 3 `beat` e 3 `renew`; `renew` falso para o gravador da live
- [x] Comando falso `liberar` → `stop_all` chamado, status `feito`, `SystemExit(75)`
- [x] Gate check passes: full

**Tests**: unit
**Gate**: full
**Commit**: `feat(monitor): heartbeat, renovacao de lease e comando liberar recursos`

---

### Phase 7: Live contínua (SERIAL: monitor.py e cli.py)



```
T25 → T26 → T27 → T28
```

### T25: [repo: clipes-lives] Laço follow_live

**What**: `follow_live(url_ou_canal, ...)`: gravador de 1 canal, fecha janela a cada `STEP` via `window_end`, monta job como `capture_job`, filtra clipes com `owns` + `filter_emitted`, fim por 2 sinais ou 1 por 300 s, piso de disco, backlog > 2 → `lost`, `source.mkv` apagado após cada janela.
**Where**: `clipes/monitor.py`
**Depends on**: None
**Reuses**: `RollingRecorderPool`, `capture_job`, `janelas.py`, `probe_metadata`
**Requirement**: LIVE-02, LIVE-01
**Implementador**: Claude Sonnet 5.5 | **Revisor**: Gemini 4 Hydrogen

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] Gravador falso com 3 janelas e depois parado + sem segmentos 90 s → 3 jobs + live `ended`
- [x] `disk_usage` falso com 4 GB → janela pulada com log; 4 janelas pendentes → a mais antiga `lost`
- [x] Gate check passes: full

**Tests**: unit
**Gate**: full
**Commit**: `feat(live): clipa a live em janelas ate ela acabar`

---

### T26: [repo: clipes-lives] Opção --ate-acabar no clipar

**What**: `--ate-acabar` no parser; com live ativa delega a `follow_live`; `--live-minutes` continua igual.
**Where**: `clipes/cli.py`
**Depends on**: T25
**Reuses**: `build_parser`, `run_pipeline`
**Requirement**: LIVE-02
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] `follow_live` falso recebe a chamada; sem a opção, fluxo atual intacto
- [x] Gate check passes: full

**Tests**: unit
**Gate**: full
**Commit**: `feat(cli): opcao --ate-acabar para live continua`

---

### T27: [repo: clipes-lives] Opção --follow no monitor

**What**: `--follow <canal>` roda `follow_live` só para o canal (respeitando `policy.json`).
**Where**: `clipes/monitor.py`
**Depends on**: T26
**Reuses**: `main` do monitor
**Requirement**: LIVE-03
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] Canal bloqueado em `policy.json` → sai 2 sem gravar
- [x] Gate check passes: full

**Tests**: unit
**Gate**: full
**Commit**: `feat(monitor): opcao --follow para um canal`

---

### T28: [repo: clipes-lives] Guia de instalação multi-PC

**What**: README: `segredos.env` (600) fora do repo; Atlas M0 com `0.0.0.0/0`, usuários `workers` e `painel` (role mínima) criados pelo usuário; deploy do `painel/` na Vercel com `PAINEL_SENHA`/`PAINEL_MONGO_URI`; Docker Desktop + WSL2 no PC2/PC3 com `.wslconfig`; `bench-live` em cada PC antes de subir `max_lives`; queries de diagnóstico e purga escritas para o usuário rodar (não executadas).
**Where**: `README.md`
**Depends on**: T27
**Reuses**: README atual
**Requirement**: FILA-03, PAINEL-02, CAP-01, PERF-01
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] Nenhum segredo, hostname, IP ou connection string no texto; exemplos fictícios
- [x] Gate check passes: build (fim da onda)

**Tests**: none
**Gate**: build
**Commit**: `docs: guia de fila multi-PC, painel na Vercel e Windows`

---

## Phase Execution Map

```
Fase 1:  T1  ‖  T2  ‖  T3  ‖  T4 ⇒ T5
Fase 2:  T6 ⇒ T7 ⇒ T8 ⇒ T9
Fase 3:  T10  ‖  T11  ‖  T12  ‖  T13  ‖  T14
Fase 4:  T15 ⇒ T16
Fase 5:  T17 ⇒ T18 ⇒ T19 ⇒ T20 ⇒ T21
Fase 6:  T22 ⇒ T23 ⇒ T24
Fase 7:  T25 ⇒ T26 ⇒ T27 ⇒ T28
```

‖ = paralelo permitido (arquivos disjuntos); ⇒ = serial.

Lotes: L1 = Fase 1 (até 4 workers, um por cadeia); L2 = Fases 2 + 3 (coord.py num worker, demais em outro); L3 = Fases 4 + 5; L4 = Fases 6 + 7 (1 worker, serial). Verifier ao fim, modelo diferente do autor.

## Task Granularity Check

| Task | Scope | Status |
| ---- | ----- | ------ |
| T1 | 1 arquivo: `vigia-temp` | ✅ |
| T2 | 1 arquivo: `clipes/capacidade.py` (novo) | ✅ |
| T3 | 1 arquivo: `clipes/janelas.py` (novo) | ✅ |
| T4 | 1 arquivo: `entrypoint.sh` | ✅ |
| T5 | 1 arquivo: `Dockerfile` | ✅ |
| T6 | 1 arquivo: `clipes/coord.py` (novo) | ✅ |
| T7 | 1 arquivo: `clipes/coord.py` | ✅ |
| T8 | 1 arquivo: `clipes/coord.py` | ✅ |
| T9 | 1 arquivo: `clipes/coord.py` | ✅ |
| T10 | 1 arquivo: `clipes/asr.py` | ✅ |
| T11 | 1 arquivo: `clipes/topics.py` | ✅ |
| T12 | 1 arquivo: `clipes/llm.py` | ✅ |
| T13 | 1 arquivo: `clipar` | ✅ |
| T14 | 1 arquivo: `docker-compose.yml` | ✅ |
| T15 | 1 arquivo: `clipes/perfil.py` (novo) | ✅ |
| T16 | 1 arquivo: `clipes/__main__.py` | ✅ |
| T17 | 1 arquivo: `painel/api/_nucleo.py` (novo) | ✅ |
| T18 | 1 arquivo: `painel/api/status.py` (novo) | ✅ |
| T19 | 1 arquivo: `painel/api/comando.py` (novo) | ✅ |
| T20 | 1 arquivo: `painel/index.html` (novo) | ✅ |
| T21 | 1 arquivo: `painel/vercel.json` (novo) | ✅ |
| T22 | 1 arquivo: `clipes/monitor.py` | ✅ |
| T23 | 1 arquivo: `clipes/monitor.py` | ✅ |
| T24 | 1 arquivo: `clipes/monitor.py` | ✅ |
| T25 | 1 arquivo: `clipes/monitor.py` | ✅ |
| T26 | 1 arquivo: `clipes/cli.py` | ✅ |
| T27 | 1 arquivo: `clipes/monitor.py` | ✅ |
| T28 | 1 arquivo: `README.md` | ✅ |

## Diagram-Definition Cross-Check

| Task | Depends On (task body) | Diagram Shows | Status |
| ---- | ---------------------- | ------------- | ------ |
| T1 | None | início/sem seta | ✅ |
| T2 | None | início/sem seta | ✅ |
| T3 | None | início/sem seta | ✅ |
| T4 | None | início/sem seta | ✅ |
| T5 | T4 | T4 → T5 | ✅ |
| T6 | None | início/sem seta | ✅ |
| T7 | T6 | T6 → T7 | ✅ |
| T8 | T7 | T7 → T8 | ✅ |
| T9 | T8 | T8 → T9 | ✅ |
| T10 | None | início/sem seta | ✅ |
| T11 | None | início/sem seta | ✅ |
| T12 | None | início/sem seta | ✅ |
| T13 | None | início/sem seta | ✅ |
| T14 | None | início/sem seta | ✅ |
| T15 | None | início/sem seta | ✅ |
| T16 | T15 | T15 → T16 | ✅ |
| T17 | None | início/sem seta | ✅ |
| T18 | T17 | T17 → T18 | ✅ |
| T19 | T18 | T18 → T19 | ✅ |
| T20 | T19 | T19 → T20 | ✅ |
| T21 | T20 | T20 → T21 | ✅ |
| T22 | None | início/sem seta | ✅ |
| T23 | T22 | T22 → T23 | ✅ |
| T24 | T23 | T23 → T24 | ✅ |
| T25 | None | início/sem seta | ✅ |
| T26 | T25 | T25 → T26 | ✅ |
| T27 | T26 | T26 → T27 | ✅ |
| T28 | T27 | T27 → T28 | ✅ |

## Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
| ---- | --------------------------- | --------------- | --------- | ------ |
| T1 | `vigia-temp` | unit | unit | ✅ |
| T2 | `clipes/capacidade.py` (novo) | unit | unit | ✅ |
| T3 | `clipes/janelas.py` (novo) | unit | unit | ✅ |
| T4 | `entrypoint.sh` | none | none | ✅ |
| T5 | `Dockerfile` | none | none | ✅ |
| T6 | `clipes/coord.py` (novo) | integration | integration | ✅ |
| T7 | `clipes/coord.py` | integration | integration | ✅ |
| T8 | `clipes/coord.py` | integration | integration | ✅ |
| T9 | `clipes/coord.py` | integration | integration | ✅ |
| T10 | `clipes/asr.py` | unit | unit | ✅ |
| T11 | `clipes/topics.py` | unit | unit | ✅ |
| T12 | `clipes/llm.py` | unit | unit | ✅ |
| T13 | `clipar` | unit | unit | ✅ |
| T14 | `docker-compose.yml` | none | none | ✅ |
| T15 | `clipes/perfil.py` (novo) | unit | unit | ✅ |
| T16 | `clipes/__main__.py` | unit | unit | ✅ |
| T17 | `painel/api/_nucleo.py` (novo) | integration | integration | ✅ |
| T18 | `painel/api/status.py` (novo) | integration | integration | ✅ |
| T19 | `painel/api/comando.py` (novo) | integration | integration | ✅ |
| T20 | `painel/index.html` (novo) | none | none | ✅ |
| T21 | `painel/vercel.json` (novo) | none | none | ✅ |
| T22 | `clipes/monitor.py` | unit | unit | ✅ |
| T23 | `clipes/monitor.py` | unit | unit | ✅ |
| T24 | `clipes/monitor.py` | unit | unit | ✅ |
| T25 | `clipes/monitor.py` | unit | unit | ✅ |
| T26 | `clipes/cli.py` | unit | unit | ✅ |
| T27 | `clipes/monitor.py` | unit | unit | ✅ |
| T28 | `README.md` | none | none | ✅ |
