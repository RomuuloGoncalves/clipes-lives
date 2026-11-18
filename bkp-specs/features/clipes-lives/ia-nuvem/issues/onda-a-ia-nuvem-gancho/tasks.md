# Onda A Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path. The skill is the source of truth for the full flow (per-task cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user - do not proceed without it.**

Regras do projeto (valem para toda task): nunca acessar banco; nada instalado no host (tudo no contêiner); nenhuma chamada real ao Gemini em teste; nenhuma API paga (rodada 3: 100% gratuito) (só `urlopen` falso); nenhuma chave em arquivo versionado, log ou spec; commit atômico local só no repo `clipes-lives`, sem push; nada de benchmark pesado (PC de piso desliga sob 6 threads).

---

**Design**: `features/clipes-lives/ia-nuvem/issues/onda-a-ia-nuvem-gancho/design.md`
**Status**: Draft

---

## Test Coverage Matrix

> Generated from codebase, project guidelines, and spec - confirm before Execute. Guidelines found: `pesquisa/REGRAS-DEV.md` (testes no contêiner, fakes, sem rede), `pyproject.toml` (`[tool.pytest.ini_options]`, sem limiar de cobertura). Sem linter configurado.

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| ---------- | ------------------ | -------------------- | ---------------- | ----------- |
| Cliente de nuvem (`cloud_llm.py`) | unit | Todos os ramos; 1:1 com NUV-01..04; todos os edge cases | `tests/test_cloud_llm.py` | quick |
| Enriquecimento (`llm.py`) | unit | Ramos nuvem/local/heurística; campos novos; edge cases de validação | `tests/test_llm.py` | quick |
| Render (`render.py`) | unit | `slugify`, `clip_stem`, evento `Hook` (posição, tempo, opt-out) | `tests/test_render_eval.py` | quick |
| CLI (`cli.py`) | unit | Manifesto, `.descricao.txt`, linha de privacidade com pipeline falso | `tests/test_cli_llm.py` | quick |
| Modelos (`models.py`) | unit | Defaults dos campos novos | `tests/test_models.py` | quick |
| Infra (`docker-compose.yml`, `clipar`) | none | build gate only | - | build |

## Gate Check Commands

> Generated from codebase - confirm before Execute. Rodar na raiz da worktree; a imagem `clipes-lives:latest` já existe (esta onda não muda o Dockerfile).

| Gate Level | When to Use | Command |
| ---------- | ----------- | ------- |
| Quick | Após tasks com testes unitários | `docker compose run --rm -v "$PWD/clipes:/app/clipes" -v "$PWD/tests:/app/tests" test pytest -q tests/<arquivo_da_task>` |
| Full | Fim de cada fase | `docker compose run --rm -v "$PWD/clipes:/app/clipes" -v "$PWD/tests:/app/tests" test pytest -q` |
| Build | Tasks de infra e fim da onda | `docker compose config -q && sh -n clipar && docker compose run --rm -v "$PWD/clipes:/app/clipes" -v "$PWD/tests:/app/tests" test sh -c "python -m compileall -q clipes && pytest -q"` |

---

## Execution Plan

Phases run in sequence. Fase 1 tem arquivos disjuntos dos arquivos núcleo: T1/T2 (`cloud_llm.py`) podem rodar em paralelo com T3 (`models.py`) e T4 (`docker-compose.yml`) e T5 (`clipar`). Da Fase 2 em diante tudo mexe em `llm.py`, `render.py` ou `cli.py`: SERIAL.

## Task Breakdown

### Phase 1: Fundação (paralelo entre cadeias)

```
T1 → T2
T3
T4
T5
```

### T1: [repo: clipes-lives] Cliente Gemini em cascata (modelo x chave)

**What**: Criar `clipes/cloud_llm.py` com `complete_cloud` (só Gemini nesta task), `cloud_enabled`, `mask`, `reset_round`, `providers_used`.
**Where**: `clipes/cloud_llm.py` (novo; testes no arquivo novo de teste do módulo)
**Depends on**: None
**Reuses**: `_THINK_RE` e padrão de `complete()` em `clipes/llm.py:289`
**Requirement**: NUV-01, NUV-04
**Implementador**: Claude Sonnet 5.5 | **Revisor**: Gemini 4 Hydrogen

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] Ordem modelo x chave respeitada; par que falhou (429/5xx/timeout/JSON inválido/404) não é repetido na rodada
- [x] Chave só no header `x-goog-api-key`; `mask()` cobre exceções logadas
- [x] `CLIPES_LLM_BACKEND=local` ou sem chave → zero chamadas ao `urlopen` falso
- [x] Gate check passes: quick em `tests/test_cloud_llm.py`
- [x] Test count: >= 8 testes novos passam

**Tests**: unit
**Gate**: quick
**Commit**: `feat(llm): cliente Gemini em cascata de modelos e chaves`

---

### T2: [repo: clipes-lives] Cotas do Gemini free tier

**What**: Em `complete_cloud`, gravar pares com 429 `RESOURCE_EXHAUSTED` de cota diária em `$OUT_DIR/.gemini_cota.json` (caminho via `CLIPES_GEMINI_COTA_FILE`; par identificado por modelo + índice da chave, nunca o valor), pular pares esgotados até a virada do dia da cota, respeitar `CLIPES_GEMINI_MIN_INTERVAL_S` por chave, e devolver `None` sem rede quando todos estiverem esgotados.
**Where**: `clipes/cloud_llm.py` (modificar)
**Depends on**: T1
**Reuses**: estrutura de T1
**Requirement**: NUV-02
**Implementador**: Claude Sonnet 5.5 | **Revisor**: Gemini 4 Hydrogen

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`

**Done when**:

- [x] 429 diário → par esgotado no arquivo; nova rodada no mesmo dia não chama o par (fake conta 0 chamadas)
- [x] 429 por minuto → par sai só da rodada atual
- [x] Todos esgotados → `None`, log `[llm] cota gratuita do Gemini esgotada` 1x, zero chamadas
- [x] Arquivo corrompido → tratado como vazio e reescrito; valor da chave nunca aparece no arquivo
- [x] Gate check passes: quick em `tests/test_cloud_llm.py`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(llm): respeita cotas do Gemini free tier por modelo e chave`

---

### T3: [repo: clipes-lives] Campos novos no SnappedClip

**What**: Adicionar `hook_top`, `description_long`, `description_source` (str, default `""`) a `SnappedClip`.
**Where**: `clipes/models.py`
**Depends on**: None
**Reuses**: dataclass existente
**Requirement**: GANCHO-01, DESC-01
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [x] Defaults vazios; testes atuais de `tests/test_models.py` continuam passando
- [x] Gate check passes: quick em `tests/test_models.py`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(models): campos de gancho e descricao longa no clipe`

---

### T4: [repo: clipes-lives] Repassar chaves de nuvem ao contêiner

**What**: Listar sem valor no `environment` do serviço `clipes` as variáveis `GEMINI_API_KEYS`, `CLIPES_GEMINI_MODELS`, `CLIPES_GEMINI_MIN_INTERVAL_S`, `CLIPES_LLM_BACKEND`, `CLIPES_HOOK_TOP` (nenhuma variável de API paga).
**Where**: `docker-compose.yml`
**Depends on**: None
**Reuses**: bloco `environment` existente
**Requirement**: NUV-04
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [x] `docker compose config` mostra as variáveis só quando definidas no shell; nenhum valor no arquivo
- [x] Gate check passes: build

**Tests**: none
**Gate**: build
**Commit**: `chore(docker): repassa variaveis de IA em nuvem ao conteiner`

---

### T5: [repo: clipes-lives] Carregar segredos fora do repo no clipar

**What**: No `clipar`, se `${CLIPES_SECRETS:-$HOME/.config/clipes-lives/segredos.env}` existir, carregar com `set -a; . arquivo; set +a`; avisar se o modo do arquivo não for 600.
**Where**: `clipar`
**Depends on**: None
**Reuses**: script atual
**Requirement**: NUV-04
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [x] Sem o arquivo, comportamento idêntico ao atual
- [x] `segredos.env` e `*.env` cobertos pelo `.gitignore` (já tem `.env`; acrescentar `*.env`)
- [x] Gate check passes: build

**Tests**: none
**Gate**: build
**Commit**: `chore(clipar): carrega segredos de arquivo fora do repositorio`

---

### Phase 2: Núcleo do texto (SERIAL)

```
T6 → T7
```

### T6: [repo: clipes-lives] Schema e prompt com gancho de topo e descrição longa

**What**: Estender `CLIP_JSON_SCHEMA` (`gancho_topo` <= 35, `descricao_longa` <= 500) e `_build_clip_prompt` (título sobre conflito/revelação/reação, gancho pergunta/promessa sem emoji e sem desfecho, descrição 2-4 frases 150-500 sem URL); criar `pick_hook_top`, `long_paragraph_fallback` e `format_long_description`.
**Where**: `clipes/llm.py`
**Depends on**: None
**Reuses**: `validate_no_hallucinated_entities`, `_EMOJI_RE`, `format_three_line_caption`
**Requirement**: NOME-03, DESC-01, GANCHO-01
**Implementador**: Claude Sonnet 5.5 | **Revisor**: Gemini 4 Hydrogen

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`

**Done when**:

- [x] Gancho inválido (emoji, > 35, entidade inventada) cai no título cortado na fronteira de palavra ou vazio
- [x] Descrição fora de 150-500, com URL ou com entidade inventada → parágrafo de fallback; texto final na ordem título, parágrafo, crédito, live completa (+ parte), hashtags
- [x] Gate check passes: quick em `tests/test_llm.py`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(llm): gancho de topo e descricao longa no schema e no prompt`

---

### T7: [repo: clipes-lives] Cascata nuvem → local → heurística no enriquecimento

**What**: Em `enrich_clips_with_llm`, chamar `complete_cloud` por clipe; subir `llama_server_session` só quando algum clipe precisar (lazy); gravar `title_source`, `hook_top`, `description_long`, `description_source`.
**Where**: `clipes/llm.py`
**Depends on**: T6
**Reuses**: `complete_cloud` (T1/T2), laço atual de validação
**Requirement**: NUV-03, NOME-03
**Implementador**: Claude Sonnet 5.5 | **Revisor**: Gemini 4 Hydrogen

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`

**Done when**:

- [x] Nuvem OK → `llama_server_session` nunca chamado (fake conta 0 entradas)
- [x] Nuvem `None` → local; local falha → heurística; `title_source` correto em cada caso
- [x] Título ecoando a live é rejeitado também quando vem da nuvem
- [x] Gate check passes: full

**Tests**: unit
**Gate**: full
**Commit**: `feat(llm): cascata nuvem, local e heuristica por clipe`

---

### Phase 3: Render e saída (SERIAL)

```
T8 → T9 → T10
```

### T8: [repo: clipes-lives] slugify e nome clip_NN_<slug>

**What**: Criar `slugify` e `clip_stem(idx, clip)` em `render.py` e usá-los em `render_clip` (mp4, ass).
**Where**: `clipes/render.py`
**Depends on**: None
**Reuses**: `unicodedata` (stdlib), `stem` atual em `clipes/render.py:356`
**Requirement**: NOME-01, NOME-02
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [x] `slugify("Ele NÃO acreditou no que viu?!") == "ele-nao-acreditou-no-que-viu"`; corte <= 40 na fronteira de palavra; vazio → `clip_NN`
- [x] Multiparte → `clip_NN_partKofN_<slug>`
- [x] Gate check passes: quick em `tests/test_render_eval.py`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(render): nome do arquivo com slug do titulo`

---

### T9: [repo: clipes-lives] Evento TopHook no topo do ASS

**What**: `wrap_top_hook` + estilo `TopHook` e evento layer 3 em `build_ass(..., hook_top="")` de 0 a `min(5, content_dur)`, `\an5\pos(540,365)\fad(120,350)` + pop, 2 linhas de <= 18 (branca/amarela); `render_clip` repassa `clip.hook_top`; `CLIPES_HOOK_TOP=0` desliga.
**Where**: `clipes/render.py`
**Depends on**: T8
**Reuses**: `_ass_escape`, `_ass_time`, header de estilos
**Requirement**: GANCHO-02
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [x] Linha `Dialogue: 3,0:00:00.00,0:00:05.00,TopHook,...\pos(540,365)` com `ELE PERDEU TUDO?`; conteúdo de 3 s termina em 3 s; 2 linhas com `\N` e cores alternadas
- [x] Gancho vazio ou opt-out → nenhum evento TopHook
- [x] Gate check passes: quick em `tests/test_render_eval.py`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(render): gancho de topo nos primeiros 5 segundos`

---

### T10: [repo: clipes-lives] Manifesto, .descricao.txt e aviso de privacidade

**What**: Em `run_pipeline`, usar `clip_stem` no `--skip-render`, gravar `<stem>.descricao.txt`, incluir `clip_id` (`<video_id>_c<NN>`), `hook_top`, `description_long`, `description_source` no `clips.json`, e escrever no README a linha `Texto da transcrição enviado a: <provedores>` quando `providers_used()` não for vazio.
**Where**: `clipes/cli.py`
**Depends on**: T9
**Reuses**: bloco `clip_entry` em `clipes/cli.py:398`, `_write_run_readme`
**Requirement**: DESC-01, NOME-02, NOME-04, NUV-04
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [x] Pipeline falso (padrão de `tests/test_cli_llm.py`) gera `.descricao.txt` e os campos novos
- [x] Rodada sem nuvem não escreve a linha de privacidade
- [x] Gate check passes: build (fim da onda)

**Tests**: unit
**Gate**: build
**Commit**: `feat(cli): clip_id, descricao longa, gancho e aviso de envio a nuvem na saida`

---

## Phase Execution Map

```
Fase 1:  [T1 ⇒ T2] ‖ [T3] ‖ [T4] ‖ [T5]      (arquivos disjuntos)
Fase 2:  T6 ⇒ T7                              (llm.py, serial)
Fase 3:  T8 ⇒ T9 ⇒ T10                        (render.py, cli.py, serial)
```

Lotes: Lote 1 = Fase 1 (5 tasks; até 4 workers em paralelo, um por cadeia). Lote 2 = Fases 2 e 3 (5 tasks, 1 worker, serial). Verifier ao fim (Claude Sonnet 5.5, diferente do autor de cada task revisada).

---

## Task Granularity Check

| Task | Scope | Status |
| ---- | ----- | ------ |
| T1 | 1 módulo novo, 1 função pública principal | ✅ |
| T2 | 1 controle de cota na mesma função | ✅ |
| T3 | 3 campos de dataclass | ✅ |
| T4 | 1 bloco YAML | ✅ |
| T5 | 1 trecho de shell + 1 linha no `.gitignore` | ⚠️ coeso |
| T6 | schema + prompt + 2 helpers no mesmo arquivo | ⚠️ coeso |
| T7 | 1 função | ✅ |
| T8 | 2 helpers + 1 chamada | ✅ |
| T9 | 1 estilo + 1 evento | ✅ |
| T10 | 1 função (`run_pipeline`) + README | ✅ |

## Diagram-Definition Cross-Check

| Task | Depends On (task body) | Diagram Shows | Status |
| ---- | ---------------------- | ------------- | ------ |
| T1 | None | - | ✅ |
| T2 | T1 | T1 → T2 | ✅ |
| T3 | None | - | ✅ |
| T4 | None | - | ✅ |
| T5 | None | - | ✅ |
| T6 | None (fase 1 inteira antes) | - | ✅ |
| T7 | T6 | T6 → T7 | ✅ |
| T8 | None (fase 2 antes) | - | ✅ |
| T9 | T8 | T8 → T9 | ✅ |
| T10 | T9 | T9 → T10 | ✅ |

## Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
| ---- | --------------------------- | --------------- | --------- | ------ |
| T1 | Cliente de nuvem | unit | unit | ✅ |
| T2 | Cliente de nuvem | unit | unit | ✅ |
| T3 | Modelos | unit | unit | ✅ |
| T4 | Infra | none | none | ✅ |
| T5 | Infra | none | none | ✅ |
| T6 | Enriquecimento | unit | unit | ✅ |
| T7 | Enriquecimento | unit | unit | ✅ |
| T8 | Render | unit | unit | ✅ |
| T9 | Render | unit | unit | ✅ |
| T10 | CLI | unit | unit | ✅ |
