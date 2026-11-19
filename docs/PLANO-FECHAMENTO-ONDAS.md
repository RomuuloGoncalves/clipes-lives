# Plano de fechamento das Ondas A/B/C (para executar em outra máquina)

> **Histórico (superado pela onda 4 `controle-discord`):** as referências a `./clipar monitor`, `acompanhar`, `--ate-acabar`, `bench-live` e `max_lives` descrevem a CLI antiga. Hoje a CLI tem `./clipar <url>`, `./clipar servico` e `auth`; os alvos vão pelo Discord (`/live`) e os limites são `CLIPES_SLOTS`. Veja o README.

Estado: branch `onda-ab` no GitHub (`git fetch && git checkout onda-ab`). `master` continua em `c19c1f4`.
Cada item = 1 commit atômico (Conventional Commits). Teste: `docker run --rm --network none clipes-lives:latest pytest -q <arquivos>` (o `docker compose` pode falhar por falta de sub-redes; limpe com `docker network prune`). Suíte global só no passo 6.

## Situação
| Onda | Revisão | Pendência |
|---|---|---|
| A (IA nuvem, gancho, nome, descrição) | PASS (80/80) | nenhuma |
| C (Discord, publicar) | PASS na 2ª rodada (59 testes) | M1, M2, M3 e 2 lacunas baixas (passo 3) |
| B (fila Mongo, painel, capacidade, live contínua) | FAIL na 1ª rodada | achados 1, 9, 11 (passo 1); 2 só parcial |
| Painel Vercel | no ar, status livre, comando com senha | trocar usuário do banco (passo 7) |

## Passos
1. **Achados da Onda B ainda abertos** (`REV-onda-B.md`, só `clipes/monitor.py`, `clipes/cli.py` e testes `tests/test_monitor*.py`, `tests/test_cli_ate_acabar.py`):
   - **#1** `follow_live`, `--follow` (monitor) e `--ate-acabar` (cli) devem receber o `coord` quando houver `CLIPES_MONGO_URI`: `claim_live`, renovar lease a cada `CLIPES_BEAT_S` (parar o gravador se `renew` falhar), `add_job`/`start_job`/`finish_job` por janela em `close_window`, `coord=` em `run_worker_step`, backlog `lost`. Sem URI mantém a fila local. Testes: lease renovado em 3 ciclos; `db.jobs` com 1 doc por janela; 2 PCs `--follow` na mesma live → só 1 grava.
   - **#2** já tem commit `f5ca86b` (`ended=False` se não terminou). Falta o teste "pool que levanta no meio → `lives.status == "live"` e `owner is None`" e cobrir Ctrl+C e lease perdida.
   - **#9** `capacity()` (nvidia-smi, 5 s) não pode bloquear o loop: `asyncio.to_thread` ou cache (`monitor.py` ~1344/1521, `_current_limits`).
   - **#11** teste e2e com mongomock: dono cai → B reivindica → B grava sem recriar jobs de `_id` existente.
   - Os achados 3 a 8 e 10 já estão na `onda-ab` (fix-b2).
2. **Mesclar** a branch de trabalho em `onda-ab`.
3. **Onda C, restos**: M1 (`monitor.py` ~966-1025, erro de prévia tratado por clipe dentro do laço); M2 (`monitor.py` ~1734, `run_pending` sem depender de `DISCORD_BOT_TOKEN`); M3 (`docker-compose.yml` ~47-50: `profiles: [bot]` ou `restart: on-failure:3`); passar `CLIPES_OAUTH_BIND` no compose; `_default_publish` (`publicacao.py` ~650-662) repassar `refresh=` para retentar um 401 com `access_token` em cache.
4. **Reconferir a Onda B** (revisor independente, só leitura) contra `REV-onda-B.md`. Se FAIL, novo ciclo de fix.
5. **Decisão AC8 já tomada**: 3 execuções reais (`attempts` conta falhas; falha da 3ª marca `failed`). Conferir que isso está na `spec.md` como assumption.
6. **Suíte global** (esperado: 376+ passando, 1 pulado de benchmark) e **Verifier final** (escrever `validation.md`; `validate_state.py`).
7. **Segurança do banco**: criar no Atlas `clipes_workers` (readWrite só em `clipes_lives`) e `clipes_painel` (idem). Trocar a URI em `~/.config/clipes-lives/segredos.env` (modo 600) e em `PAINEL_MONGO_URI` na Vercel (`vercel env rm/add`, depois `vercel deploy --prod` em `painel/`). Hoje as duas usam um usuário `atlasAdmin`.
8. **Merge no master e push**: `git checkout master && git merge --no-ff onda-ab && git push`.

## Setup manual (não é código)
- Google Cloud: app OAuth + YouTube Data API; TikTok: app com Content Posting API (uploads ficam privados até a auditoria). Depois `clipes auth youtube` e `clipes auth tiktok`.
- Discord: bot token, Message Content Intent, `DISCORD_ALLOWED_USER_IDS` (vazio = ninguém publica).
- PCs Windows: Docker Desktop; copiar `segredos.env` com `CLIPES_MONGO_URI`.
- Bug conhecido fora do plano: vídeo `twitch:vod` é tratado como live no ingest (tratar `twitch:vod` como VOD).

## Regras
Nunca commitar chaves, URI ou senhas. Só clipar canais autorizados.
