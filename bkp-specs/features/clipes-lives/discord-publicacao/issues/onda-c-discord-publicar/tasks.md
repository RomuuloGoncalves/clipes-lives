# Onda C Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path. The skill is the source of truth for the full flow (per-task cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user - do not proceed without it.**

Regras do projeto (valem para toda task): nunca acessar banco (nenhuma conexão a URI real; testes com `mongomock`/fakes em memória; queries reais são escritas e entregues ao usuário); nada instalado no host (tudo no contêiner); nenhuma chamada de rede real em teste; nenhum segredo (URI, token, senha, chave, hostname) em arquivo versionado, log, spec ou commit; commit atômico local só no repo `clipes-lives`, sem push, sem deploy, sem publicar; nada de benchmark pesado (PC de piso desliga sob 6 threads); antes de `docker compose build` conferir `df -h /` com >= 6 GB livres e rodar `docker image prune -f` depois (não apagar volumes). Publicação real e chamadas reais a Discord/Google/TikTok são proibidas durante o desenvolvimento: só fakes.

---

**Design**: `features/clipes-lives/discord-publicacao/issues/onda-c-discord-publicar/design.md`
**Status**: Draft

---

## Test Coverage Matrix

> Generated from codebase, project guidelines, and spec - confirm before Execute. Guidelines found: `pesquisa/REGRAS-DEV.md` (testes no contêiner, fakes, sem rede), `pyproject.toml`. Sem linter configurado.

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| ---------- | ------------------ | -------------------- | ---------------- | ----------- |
| Clientes REST (`discord_rest`, `publicar_youtube`, `publicar_tiktok`, `oauth`) | unit (`urlopen` falso) | Todos os ramos dos ACs; 401/429/erro de conta; token nunca aparece em saída | `tests/test_<modulo>.py` | quick |
| Prévia (`previa.py`) | unit (`run` falso + 1 teste real curto com ffmpeg em vídeo sintético de 3 s) | Original quando cabe; fórmula do bitrate; `None` quando não cabe | `tests/test_previa.py` | quick |
| Orquestração (`publicacao.py`) | integration (mongomock e store local em tmp_path) | Permissão, política, idempotência, cota, PC offline, arquivo ausente | `tests/test_publicacao.py` | quick |
| Bot (`bot.py`) | unit (handlers chamados direto com objetos falsos, sem gateway) | Permissão, parsing de `publicar <id>`, `/listar` | `tests/test_bot.py` | quick |
| Monitor/roteamento (`monitor.py`, `__main__.py`) | unit | Pontos de chamada novos + regressão | `tests/test_monitor_*.py` | full |
| Infra (`Dockerfile`, `docker-compose.yml`, README) | none | build gate only | - | build |

## Gate Check Commands

> Generated from codebase - confirm before Execute. Rodar na raiz da worktree.

| Gate Level | When to Use | Command |
| ---------- | ----------- | ------- |
| Quick | Após tasks com testes unitários | `docker compose run --rm -v "$PWD/clipes:/app/clipes" -v "$PWD/tests:/app/tests" test pytest -q tests/<arquivo_da_task>` |
| Full | Fim de cada fase | `docker compose run --rm -v "$PWD/clipes:/app/clipes" -v "$PWD/tests:/app/tests" test pytest -q` |
| Build | Tasks de infra (Dockerfile, compose, scripts sh) e fim da onda | `df -h / && docker compose config -q && sh -n clipar && sh -n vigia-temp && docker compose build && docker compose run --rm test sh -c "python -m compileall -q clipes && pytest -q"` (o `docker compose build` só quando o Dockerfile ou o `entrypoint.sh` mudou) |

---

## Execution Plan

Fases em sequência; a Onda C só começa depois de as Ondas A e B fecharem (usa `clip_id`, `description_long` e `coord.py`). Fase 1 tem arquivos novos disjuntos: paralelo permitido. Fases 2 e 3 são seriais; `monitor.py` sempre serial.

---

## Task Breakdown

### Phase 1: Clientes isolados (paralelo: arquivos disjuntos)

Todas as cadeias desta fase podem rodar em paralelo.

```
T1
T2
T3
T4
T5
T6
```

### T1: [repo: clipes-lives] discord.py na imagem

**What**: Camada final `RUN pip install "discord.py>=2.4,<3"` e uma reconstrução.
**Where**: `Dockerfile`
**Depends on**: None
**Reuses**: camada de dependências da Onda B
**Requirement**: DISC-01, PUB-01
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] `df -h /` >= 6 GB antes; `docker image prune -f` depois
- [x] `python -c 'import discord'` no contêiner
- [x] Gate check passes: build

**Tests**: none
**Gate**: build
**Commit**: `build(docker): adiciona discord.py`

---

### T2: [repo: clipes-lives] Prévia para o limite do Discord

**What**: `preview_for_discord` com a fórmula do relatório A 5.2 em `cache/discord_<stem>.mp4`.
**Where**: `clipes/previa.py` (novo)
**Depends on**: None
**Reuses**: comando ffmpeg de `clipes/render.py:401`
**Requirement**: DISC-02
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] Arquivo de 5 MB com limite 10 → original; 25 MB → `run` falso recebe `scale=720:1280` e bitrate da fórmula
- [x] Duração tal que < 400 kbps → `None`
- [x] Teste real: vídeo sintético de 3 s (`lavfi`) recomprimido fica <= alvo
- [x] Gate check passes: quick em `tests/test_previa.py`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(discord): previa recomprimida no limite de upload`

---

### T3: [repo: clipes-lives] Cliente REST do Discord

**What**: `DiscordRest.post` (texto e multipart com arquivo) e `.thread_from`; 429 com `retry_after` até 3x; mensagens de erro sem token.
**Where**: `clipes/discord_rest.py` (novo)
**Depends on**: None
**Reuses**: padrão de `urlopen` injetável
**Requirement**: DISC-01, DISC-03
**Implementador**: Claude Sonnet 5.5 | **Revisor**: Gemini 4 Hydrogen

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] Fake confere header `Bot <token>`, `payload_json` e `files[0]`
- [x] 429, 429, 200 → sucesso com 2 esperas; 4x 429 → exceção sem o token no texto
- [x] Gate check passes: quick em `tests/test_discord_rest.py`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(discord): cliente REST para mensagens, anexos e threads`

---

### T4: [repo: clipes-lives] Tokens e renovação OAuth

**What**: `load_tokens`/`save_tokens` (atômico, 600, `/config/tokens.json`), `refresh_google`, `refresh_tiktok` (regrava refresh novo), `auth_flow` com `state` e servidor local.
**Where**: `clipes/oauth.py` (novo)
**Depends on**: None
**Reuses**: `secrets`, `http.server`
**Requirement**: AUTH-01, PUB-04
**Implementador**: Claude Sonnet 5.5 | **Revisor**: Gemini 4 Hydrogen

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] `state` errado aborta sem trocar código; certo grava arquivo modo 600
- [x] TikTok devolvendo refresh novo → arquivo atualizado
- [x] Gate check passes: quick em `tests/test_oauth.py`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(oauth): tokens fora do repo com renovacao e fluxo de autorizacao`

---

### T5: [repo: clipes-lives] Publicador do YouTube

**What**: `publish(mp4, title, description, token, urlopen)`: sessão resumível, `PUT`, até 3 retomadas, `privacyStatus="private"` literal, título `[:90] + " #Shorts"` sem `<>`, `categoryId=20`, `selfDeclaredMadeForKids=false`; recusa > 180 s.
**Where**: `clipes/publicar_youtube.py` (novo)
**Depends on**: None
**Reuses**: `oauth.refresh_google`
**Requirement**: PUB-02
**Implementador**: Claude Sonnet 5.5 | **Revisor**: Gemini 4 Hydrogen

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] Fake captura o JSON e confere `private` e o título
- [x] Queda no `PUT` → retomada e sucesso; 4 quedas → `erro`
- [x] Clipe de 181 s → `clipe acima de 3 min` sem chamada
- [x] Gate check passes: quick em `tests/test_publicar_youtube.py`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(publicar): upload privado no YouTube Shorts`

---

### T6: [repo: clipes-lives] Publicador do TikTok

**What**: `publish(mp4, caption, token, urlopen, sleep)`: `creator_info` (exige `SELF_ONLY` nas opções), `init` `FILE_UPLOAD` 1 chunk, `PUT` com `Content-Range`, status até 60x/5 s; `privacy_level="SELF_ONLY"` literal.
**Where**: `clipes/publicar_tiktok.py` (novo)
**Depends on**: None
**Reuses**: `oauth.refresh_tiktok`
**Requirement**: PUB-03
**Implementador**: Claude Sonnet 5.5 | **Revisor**: Gemini 4 Hydrogen

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] Fluxo feliz até `PUBLISH_COMPLETE`; `FAILED` → `erro`
- [x] `unaudited_client_can_only_post_to_private_accounts` → `erro_conta`
- [x] Gate check passes: quick em `tests/test_publicar_tiktok.py`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(publicar): post privado no TikTok via Content Posting API`

---

### Phase 2: Orquestração e bot (serial)



```
T7 → T8 → T9
```

### T7: [repo: clipes-lives] Pedidos de publicação

**What**: `find_clip`, `request` (permissão, política com `ready_at`, chave única `(clip_id, plataforma)`, cota local), `run_pending` (executa só pedidos do próprio `worker_id`, refresh 1x em 401, resposta na thread) com store Mongo (`Coord`) ou `LocalStore`.
**Where**: `clipes/publicacao.py` (novo)
**Depends on**: None
**Reuses**: `Coord`, `load_policy`, publicadores
**Requirement**: PUB-01, PUB-04
**Implementador**: Claude Sonnet 5.5 | **Revisor**: Gemini 4 Hydrogen

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] Usuário fora da lista → `sem permissao`; lista vazia → ninguém publica
- [x] Canal bloqueado ou antes de `ready_at` → recusa sem pedido
- [x] 2º pedido do mesmo clipe/plataforma `ok` → link existente sem chamar API
- [x] Cota local cheia → `adiado`; `.mp4` ausente → `erro_arquivo`
- [x] Gate check passes: quick em `tests/test_publicacao.py`

**Tests**: integration
**Gate**: quick
**Commit**: `feat(publicar): fila de pedidos com permissao, politica e idempotencia`

---

### T8: [repo: clipes-lives] Bot do Discord

**What**: `make_bot`: `Intents.default()` + `message_content`, sync dos slash na guild, mensagem `publicar <id>`, `/publicar id:`, `/listar`; handlers finos que chamam `publicacao.request`.
**Where**: `clipes/bot.py` (novo)
**Depends on**: T7
**Reuses**: `discord.py`, `publicacao`
**Requirement**: PUB-01
**Implementador**: Claude Sonnet 5.5 | **Revisor**: Gemini 4 Hydrogen

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] Handlers testados com mensagem/interação falsas, sem gateway
- [x] Mensagem fora do canal configurado e das threads dele é ignorada
- [x] Gate check passes: quick em `tests/test_bot.py`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(discord): bot com comandos publicar e listar`

---

### T9: [repo: clipes-lives] Subcomandos bot e auth

**What**: Rotear `python -m clipes bot` e `python -m clipes auth <youtube|tiktok>`.
**Where**: `clipes/__main__.py`
**Depends on**: T8
**Reuses**: roteamento existente
**Requirement**: PUB-01, AUTH-01
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] `auth foo` sai 2; `bot` sem `DISCORD_BOT_TOKEN` sai 2 com mensagem sem segredo
- [x] Gate check passes: quick em `tests/test_bot.py`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(cli): subcomandos bot e auth`

---

### Phase 3: Integração (SERIAL: monitor.py e infra)



```
T10 → T11 → T12
```

### T10: [repo: clipes-lives] Prévia e publicação no monitor

**What**: Após `queue.complete(..., "done")`: `previa` + `discord_rest` (cabeçalho, thread, 1 msg por clipe; grava `thread_id`/`message_id` no `clips.json`; não reposta); no heartbeat: `publicacao.run_pending`. Sem `DISCORD_BOT_TOKEN`, pula.
**Where**: `clipes/monitor.py`
**Depends on**: None
**Reuses**: `run_worker_step`, laço de heartbeat da Onda B
**Requirement**: DISC-01, PUB-01
**Implementador**: Claude Sonnet 5.5 | **Revisor**: Gemini 4 Hydrogen

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] Job com 2 clipes → 1 cabeçalho, 1 thread, 2 mensagens; rodar de novo não reposta
- [x] Falha do Discord grava `discord_error` e o worker segue
- [x] Gate check passes: full

**Tests**: unit
**Gate**: full
**Commit**: `feat(monitor): envia previas ao Discord e executa pedidos de publicacao`

---

### T11: [repo: clipes-lives] Serviço bot e volume de configuração

**What**: Serviço `bot` (`python -m clipes bot`, `restart: unless-stopped`), volume `${CLIPES_CONFIG_DIR:-$HOME/.config/clipes-lives}:/config` nos serviços, repasse sem valor das variáveis do Discord/Google/TikTok.
**Where**: `docker-compose.yml`
**Depends on**: T10
**Reuses**: serviços atuais
**Requirement**: DISC-01, AUTH-01
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] `docker compose config` sem valores de segredo
- [x] Gate check passes: build

**Tests**: none
**Gate**: build
**Commit**: `chore(docker): servico do bot e configuracao fora do repo`

---

### T12: [repo: clipes-lives] Guia de portais e auditoria

**What**: README: Discord Developer Portal (bot, intent Message Content, escopos `bot` + `applications.commands`, permissões), Google Cloud (API YouTube Data v3, OAuth app instalado, tela em "In production"), TikTok for Developers (Content Posting API, `video.publish`, conta privada), o que cada auditoria exige, `clipes auth`; tudo sem valores reais.
**Where**: `README.md`
**Depends on**: T11
**Reuses**: relatório A seções 5.4 e 6.2
**Requirement**: AUTH-01, PUB-02, PUB-03
**Implementador**: Gemini 4 Hydrogen | **Revisor**: Claude Sonnet 5.5

**Tools**:

- MCP: NONE
- Skill: `tlc-spec-driven`, `ponytail:ponytail`

**Done when**:

- [x] Nenhum token, ID secreto ou e-mail no texto (IDs de servidor/canal só no env)
- [x] Gate check passes: build (fim da onda)

**Tests**: none
**Gate**: build
**Commit**: `docs: guia do bot do Discord e publicacao privada`

---

## Phase Execution Map

```
Fase 1:  T1  ‖  T2  ‖  T3  ‖  T4  ‖  T5  ‖  T6
Fase 2:  T7 ⇒ T8 ⇒ T9
Fase 3:  T10 ⇒ T11 ⇒ T12
```

‖ = paralelo permitido (arquivos disjuntos); ⇒ = serial.

Lotes: L1 = Fase 1 (até 4 workers, um por cadeia); L2 = Fases 2 + 3 (coord.py num worker, demais em outro); L3 = Fases 4 + 5; L4 = Fases 6 + 7 (1 worker, serial). Verifier ao fim, modelo diferente do autor.

## Task Granularity Check

| Task | Scope | Status |
| ---- | ----- | ------ |
| T1 | 1 arquivo: `Dockerfile` | ✅ |
| T2 | 1 arquivo: `clipes/previa.py` (novo) | ✅ |
| T3 | 1 arquivo: `clipes/discord_rest.py` (novo) | ✅ |
| T4 | 1 arquivo: `clipes/oauth.py` (novo) | ✅ |
| T5 | 1 arquivo: `clipes/publicar_youtube.py` (novo) | ✅ |
| T6 | 1 arquivo: `clipes/publicar_tiktok.py` (novo) | ✅ |
| T7 | 1 arquivo: `clipes/publicacao.py` (novo) | ✅ |
| T8 | 1 arquivo: `clipes/bot.py` (novo) | ✅ |
| T9 | 1 arquivo: `clipes/__main__.py` | ✅ |
| T10 | 1 arquivo: `clipes/monitor.py` | ✅ |
| T11 | 1 arquivo: `docker-compose.yml` | ✅ |
| T12 | 1 arquivo: `README.md` | ✅ |

## Diagram-Definition Cross-Check

| Task | Depends On (task body) | Diagram Shows | Status |
| ---- | ---------------------- | ------------- | ------ |
| T1 | None | início/sem seta | ✅ |
| T2 | None | início/sem seta | ✅ |
| T3 | None | início/sem seta | ✅ |
| T4 | None | início/sem seta | ✅ |
| T5 | None | início/sem seta | ✅ |
| T6 | None | início/sem seta | ✅ |
| T7 | None | início/sem seta | ✅ |
| T8 | T7 | T7 → T8 | ✅ |
| T9 | T8 | T8 → T9 | ✅ |
| T10 | None | início/sem seta | ✅ |
| T11 | T10 | T10 → T11 | ✅ |
| T12 | T11 | T11 → T12 | ✅ |

## Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
| ---- | --------------------------- | --------------- | --------- | ------ |
| T1 | `Dockerfile` | none | none | ✅ |
| T2 | `clipes/previa.py` (novo) | unit | unit | ✅ |
| T3 | `clipes/discord_rest.py` (novo) | unit | unit | ✅ |
| T4 | `clipes/oauth.py` (novo) | unit | unit | ✅ |
| T5 | `clipes/publicar_youtube.py` (novo) | unit | unit | ✅ |
| T6 | `clipes/publicar_tiktok.py` (novo) | unit | unit | ✅ |
| T7 | `clipes/publicacao.py` (novo) | integration | integration | ✅ |
| T8 | `clipes/bot.py` (novo) | unit | unit | ✅ |
| T9 | `clipes/__main__.py` | unit | unit | ✅ |
| T10 | `clipes/monitor.py` | unit | unit | ✅ |
| T11 | `docker-compose.yml` | none | none | ✅ |
| T12 | `README.md` | none | none | ✅ |
