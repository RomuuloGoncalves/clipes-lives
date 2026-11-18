# REV-onda-C: revisão independente da Onda C (bot do Discord e publicação privada)

**Branch revisado:** `onda-ab` (worktree `rev-c`, HEAD `ca443eb`)
**Escopo:** `clipes/previa.py`, `discord_rest.py`, `oauth.py`, `publicar_youtube.py`, `publicar_tiktok.py`, `publicacao.py`, `bot.py`, `__main__.py`, `monitor.py` (prévia e publicação), `docker-compose.yml`, `Dockerfile`, `README.md`.
**Método:** leitura integral dos módulos contra `spec.md` e `design.md`; execução só dos 8 arquivos de teste da onda; 3 experimentos isolados com HTTP/publicadores falsos no contêiner (nada real, sem push, sem editar código).

## Veredito: **FAIL**

Os 63 testes da onda passam (`63 passed`), mas 3 defeitos impedem o uso real e 1 viola a idempotência exigida. Todos os testes injetam `refreshers=` e `publishers=` falsos, então os caminhos reais nunca foram exercitados.

## Achados

### Bloqueantes

**B1 (bug) — a renovação de token padrão sempre falha, então nenhuma publicação real funciona.**
`clipes/publicacao.py:556-566` (`_default_refresh`) devolve o retorno de `oauth.refresh_google`/`refresh_tiktok`, que é `str` (`oauth.py:144-231`). `do_refresh` (`publicacao.py:750-764`) faz `refreshed.get("access_token")` nessa `str`. O `tokens.json` só guarda `refresh_token` (`oauth.py:373-394`), então o caminho `publicacao.py:766-776` sempre cai nessa renovação.
- Evidência (experimento 2, `refresh_google` com o mesmo contrato do real, sem `refreshers` injetado): `erro_auth {'erro': "'str' object has no attribute 'get'"}`.
- Efeito: todo pedido real termina `erro_auth` com "refaca clipes auth". Quebra PUB-02, PUB-03 e PUB-04 fora dos testes.
- Sugestão: `do_refresh` aceitar `str` (ou `_default_refresh` devolver `{"access_token": ...}`). Adicionar teste sem injetar `refreshers`, monkeypatchando só `oauth.refresh_*` e o `urlopen`.

**B2 (bug) — `clipes auth` no Docker nunca recebe o callback do navegador (AUTH-01).**
`oauth.py:265` escuta em `127.0.0.1` dentro do contêiner. O README (`README.md:211-212`) manda usar `docker compose run --rm -p 127.0.0.1:8765:8765 clipes auth ...`. O Docker encaminha para a interface do contêiner, não para o loopback dele.
- Evidência (experimento 3): `python -m http.server --bind 127.0.0.1` dentro da imagem, publicado com `-p 127.0.0.1:18765:8765`, dá `curl: (56)` (conexão resetada).
- Efeito: o fluxo documentado trava até o timeout de 180 s e nenhum token é gravado.
- Sugestão: variável/flag para escutar em `0.0.0.0` dentro do contêiner (o `-p 127.0.0.1:...` do host já limita a exposição) ou documentar rodar `clipes auth` no host. Cobrir com teste do bind.

**B3 (bug/segurança) — publicação duplicada sob concorrência; idempotência por `(clip_id, plataforma)` não vale na execução.**
`run_pending` (`publicacao.py:702-784`) lê `pending` e publica sem reivindicar o pedido (não grava `running` antes do upload). No modo local, o bot dispara `run_pending` em `asyncio.to_thread` a cada comando (`bot.py:203-215`, `231-256`), e o segundo `publicar` do mesmo clipe devolve o pedido `pending` existente (`publicacao.py:258`) e dispara outra thread.
- Evidência (experimento 1): 2 threads de `run_pending` sobre 1 pedido `pending` → `UPLOADS: 2`. Também `FileNotFoundError` em `os.replace`, porque o arquivo temporário `.<nome>.tmp.<pid>` (`publicacao.py:42`) é o mesmo para as duas threads do mesmo processo.
- Efeito: dois uploads do mesmo clipe, que o PUB-01 AC5 e a meta da spec proíbem, e crash na gravação do estado.
- Sugestão: transição atômica `pending → running` antes de publicar (Mongo `find_one_and_update`; Local com `threading.Lock` + `fcntl`), temporário único (`tempfile.mkstemp`), e teste com 2 execuções concorrentes.

### Segurança / spec

**S1 (spec/segurança) — `/publicar` e `/listar` não verificam canal/threads configurados.**
`bot.py:296-312` não chama `_in_configured_channel`; só `on_message` (`bot.py:239`) restringe. O slash funciona em qualquer canal do servidor (a lista de usuários ainda protege o `/publicar`, mas `/listar` mostra títulos e `clip_id` a qualquer um). Requisito do revisor: "comando só no canal/threads configurados".
Sugestão: no slash, responder `canal nao permitido` (efêmero) fora do canal/thread configurado, com teste.

**S2 (segurança) — canal vazio ignora a `policy.json`.**
`publicacao.py:494-496`: `if channel and not pol.permits(channel)`. Clipe sem canal extraível (ex.: `clips.json` sem `channel`, sem job em `queue/done`) passa mesmo com `allowed` restritivo.
- Evidência (experimento 1): com `policy.json` `{"allowed":["gaules"]}` e clipe sem canal, `request` devolve `publicacao enfileirada`. O mesmo vale no `run_pending` (`publicacao.py:737-738`).
- Sugestão: com `allowed` não vazio e canal desconhecido, bloquear (`bloqueado pela politica`).

### Menores

**M1 (bug, baixo) — um erro em um clipe aborta os seguintes na prévia.** O `try` em `monitor.py:985-1031` envolve o laço inteiro, então um 413/ffmpeg num clipe deixa os demais sem prévia e sem nova tentativa. Sugestão: tratar por clipe, registrando `discord_error` por clipe.

**M2 (spec, baixo) — o executor depende de `DISCORD_BOT_TOKEN` no PC dono.** `monitor.py:1735` só roda `run_pending` se o token existir. Um PC worker sem o token deixa o pedido `pending` para sempre. Documentar no README (já há o token em cada `segredos.env`) ou desacoplar do token (só a resposta na thread precisa dele).

**M3 (bug, baixo) — `bot` com `restart: unless-stopped` sem `DISCORD_BOT_TOKEN`** sai com código 2 e reinicia em laço (`docker-compose.yml`, `bot.py:327-330`). O comportamento sem token não quebra (mensagem limpa, sem segredo), mas gera crash-loop. Sugestão: perfil `profiles: [bot]` ou `restart: on-failure:3`.

**M4 (bug, baixo) — `_is_401_error` casa a substring `"401"` em qualquer texto** (`publicacao.py:604-605`), podendo classificar mal um erro que contenha esse número (ex.: um `clip_id`).

**M5 (nota) — cotas.** Contador por PC (`.cotas_publicacao.json` em `out_root`) e dia em UTC; a cota do YouTube zera no fuso do Pacífico. Cumpre o AC 10 (conservador), mas não é global entre PCs; vale citar no README.

**M6 (spec, baixo) — a descrição do YouTube não remove `<`/`>`;** só o título (`publicar_youtube.py:33-47`). A edge case da spec cita só o título.

## Cobertura por critério da spec

| Critério | Teste que afirma o resultado | Situação |
| --- | --- | --- |
| DISC-01 (AC1, 2, 6, 7) | `test_monitor_discord::test_job_done_2_clipes_...`, `test_sem_discord_bot_token_pula_previa_e_run_pending` | OK |
| DISC-02 (AC3, 4) | `test_previa.py` (5 testes, inclui ffmpeg real) e `test_previa_recompressao_e_arquivo_grande_demais_sem_anexo` | OK (fórmula 400/2500 kbps, alvo 90%, `None` abaixo de 400 kbps; M1 é lacuna de robustez) |
| DISC-03 (AC5) | `test_discord_rest` (retry 3x, 4º 429 levanta sem token) e `test_falha_discord_429_grava_discord_error...` | OK |
| PUB-01 (AC1-5, 10) | `test_publicacao` (permissão, lista vazia, não encontrado, política, `ready_at`, ok repetido, cota `adiado`, offline) | **Parcial**: sem teste de concorrência (B3), de canal vazio (S2) e de canal do slash (S1) |
| PUB-02 (AC6, 9) | `test_publicar_youtube` (corpo `private`, `<>`, 180 s, retomada) | OK; sem parâmetro que mude a privacidade (literal `PRIVACY_STATUS`) |
| PUB-03 (AC7, 8, 9) | `test_publicar_tiktok` (`SELF_ONLY`, 60 consultas/5 s, `erro_conta`) | OK |
| PUB-04 (AC11, 12) | `test_publicacao` (401, refresh único, rotação, sem vazar segredos) | **Parcial**: só com `refreshers` falso; o caminho real está quebrado (B1) |
| AUTH-01 | `test_oauth` (state errado aborta, modo 600, "token salvo") | Lógica OK; o fluxo no Docker está quebrado (B2) |

## Itens verificados sem achado

- **Segredos:** `DiscordRest.__repr__` mascara o token; erros do REST, OAuth e monitor fazem `replace(token, "***")`; `_sanitize_text` remove URLs com `upload`; mensagens dos publicadores só trazem status HTTP/código; nenhum arquivo versionado contém token (`tokens.json` fica em `/config`, fora do repo).
- **tokens.json:** `mkstemp` + `fchmod 0600` + `fsync` + `os.replace` (`oauth.py:55-73`); teste de modo 600 presente. O TikTok regrava o refresh rotacionado.
- **Permissão:** `DISCORD_ALLOWED_USER_IDS` vazio ou ausente → ninguém publica (`publicacao.py:67-71`, `483`); `on_message` ignora bots, canal diferente e threads de outros canais; `DISCORD_CHANNEL_ID` ausente → ignora tudo.
- **Privacidade fixa:** `PRIVACY_STATUS="private"` e `PRIVACY_LEVEL="SELF_ONLY"` literais, sem env nem parâmetro; TikTok ainda exige `SELF_ONLY` no `creator_info`.
- **Política e atraso:** `permits` e `ready_at`/`delay_h` aplicados no pedido e na execução (ressalva S2).
- **Prévia:** recompressão ≤ 90% do limite, 720x1280, mensagem `arquivo grande demais para o Discord` sem anexo, `message_id` evita repostagem.
- **Sem `DISCORD_BOT_TOKEN`:** monitor pula prévia e `run_pending` sem erro (teste dedicado); `clipes bot` sai com 2 sem expor segredo (ver M3).
- **Testes pré-existentes:** a única alteração de asserção é `test_render_eval.py` (nome do arquivo com slug, ligada ao commit `0219d44` da Onda A, fora desta onda); não há afrouxamento.

## Comandos e evidências

- Testes: `docker run ... pytest tests/test_previa.py test_discord_rest.py test_oauth.py test_publicar_youtube.py test_publicar_tiktok.py test_publicacao.py test_bot.py test_monitor_discord.py` → `63 passed` (suíte global não rodada, conforme pedido).
- Experimentos descartáveis em `/tmp/claude-1000/.../scratchpad/exp.py` e `exp2.py` (sem rede, publicadores falsos); nada no repositório foi alterado.

## Recomendação

Corrigir B1, B2 e B3 (cada um com teste que falhe antes da correção), depois S1 e S2, e reverificar. M1-M6 podem virar tarefas pequenas na mesma onda ou ficar registradas.

FIM
