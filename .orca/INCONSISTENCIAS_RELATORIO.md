# Relatório de inconsistências: clipes-lives

Auditoria somente leitura feita em 2026-11-18/19 sobre o branch `master` (HEAD `8177cce`). Nenhum arquivo do app foi alterado. Os caminhos são relativos a `C:\Workspace\clipes-lives`.

## Sumário executivo

| Severidade | Qtde |
|---|---|
| Crítica | 2 |
| Alta | 7 |
| Média | 22 |
| Baixa | 25 |
| **Total** | **56** |

Revisão de fontes: um agente Antigravity com o modelo `argon` (via Orca) conferiu o arquivo:linha de todos os itens. Resultado: 55 de 55 confirmados, 1 sub-achado falso positivo (`cache_dir`, removido de B-21), 1 referência errada (B-13, corrigida), pequenos desvios de intervalo de linhas (já ajustados) e 6 achados novos. Desses, 5 foram incorporados a itens existentes (M-04, M-13, M-18, M-21, B-24) e 1 virou item novo (B-25).

Os workers levantaram 105 achados brutos. O revisor removeu duplicatas (as principais: `running` preso, `run_pending` no heartbeat, lives órfãs, bot síncrono e `liberar` aparecem em 2 ou 3 relatórios) e descartou 6 falsos positivos (ver o fim do relatório).

O que mais pesa:

1. **Captura do YouTube:** se o gravador cai, a live é dada como encerrada em cerca de 90 s, e o Mongo a marca como `ended` para sempre. O gravador não tenta reconectar.
2. **Leases:** a renovação fica bloqueada atrás de uploads (`monitor`) e de chamadas síncronas longas (`acompanhar`). A lease expira, outro PC assume e os jobs já capturados viram `lost`/`coord_denied`.
3. **Publicação:** um pedido pode ficar preso em `running` para sempre. Os pedidos agendados nunca rodam no modo `acompanhar`. A cota é contada em UTC, mas o YouTube a zera à meia-noite do horário do Pacífico.
4. **Duplicação:** janelas de 600 s com passo de 300 s mandam ao Discord e ao registro todos os clipes do pipeline, sem passar pelo filtro `owns`/`filter_emitted`.

Estado dos testes: na imagem Docker (cópia do repo montada só para leitura) passaram 510 e 1 foi pulado. No Windows nativo a suíte não roda, porque `fcntl` só existe no Linux. O ruff aponta 21 erros da família F e o mypy (`--platform linux`) aponta 29.

---

## Crítica

### C-01 Queda momentânea do gravador encerra a live e a marca como `ended` de vez
- **Domínio:** YouTube/Vídeo
- **Local:** `clipes/acompanhar.py:362-374`, `clipes/acompanhar.py:212-214`, `clipes/monitor.py:478-484`, `clipes/acompanhar.py:889-890` → `clipes/acompanhar.py:755`, `clipes/coord.py:66-70`, `clipes/coord.py:50`
- **Problema:**
  - As sessões do `acompanhar` usam `start_on_demand`, que devolve `True` se o canal já está em `recordings`, mesmo com o processo morto. Nada reinicia o gravador, porque só `set_hot` faz isso e o pool é criado com `max_hot=0`.
  - Quando o yt-dlp ou o streamlink cai (rede, URL HLS expirada depois de cerca de 6 h, erro de fragmento), o sinal `exited` liga na hora. Cerca de 90 s depois liga `silent`, que vem da mesma causa.
  - A regra `len(self.since) >= 2` encerra a sessão sem esperar `LIVE_END_PERSIST_S`.
  - `release(live_id, ended=True)` grava `status="ended"`. Como `claim_live` filtra `status: "live"` e `ensure_live` usa `$setOnInsert`, nenhum PC consegue mais reassumir essa live.
- **Correção:**
  - Em `LiveSession.tick`, quando `exited` ligar e `is_live` ainda for verdadeiro, reiniciar o gravador (`_stop` + `_try_start`) com backoff.
  - Contar `exited` e `silent` como um sinal só, e exigir `offline` confirmado para encerrar.
  - Gravar `ended=True` apenas quando a plataforma confirmar `is_live=False`.
  - Adicionar `--retries infinite --fragment-retries infinite --socket-timeout` em `ytdlp_live_cmd` (`clipes/monitor.py:370-374`).

### C-02 Uploads dentro do `heartbeat_loop` bloqueiam a renovação de lease
- **Domínio:** Filas/Concorrência
- **Local:** `clipes/monitor.py:1476-1538`, com o trecho principal em `:1521-1530`. Efeitos em `clipes/coord.py:56-57` (`mark_lost`) e `clipes/monitor.py:960-962` (`coord_denied`).
- **Problema:** `heartbeat_loop` é o único lugar que chama `renew`, e logo depois faz `await asyncio.to_thread(publicacao.run_pending, ...)`. Essa chamada sobe para YouTube e TikTok, em série, todos os pedidos vencidos. Com `CLIPES_LEASE_S=90` e `CLIPES_BEAT_S=20`, um ou dois uploads já deixam a lease expirar.
- **Consequência:** outro PC chama `claim_live`, recebe o dono antigo em `ReturnDocument.BEFORE` e executa `mark_lost`. Os jobs deste PC viram `lost`, e o worker local passa a rejeitá-los com `coord_denied`, perdendo o `source.mkv` já capturado. Durante a sobreposição, os dois PCs gravam a mesma live.
- **Correção:**
  - Tirar `run_pending` do heartbeat e rodá-lo numa task ou thread própria, com uma trava de "já rodando".
  - Deixar o heartbeat só com `beat`, `renew` e comandos.
  - Garantir `lease_s >= 3 * beat_s` e limitar quantos pedidos cada chamada processa.

---

## Alta

### A-01 Pedido de publicação preso em `running` para sempre
- **Domínio:** Filas/Concorrência
- **Local:** `clipes/publicacao.py:28` (`_ACTIVE_REQUEST_STATUSES`), `:387-398` e `:491-502` (`claim_request`), `:1154-1322` (`try` só com `finally`)
- **Problema:**
  - `claim_request` grava `running` sem lease e sem horário de início.
  - Exceções fora dos `try` internos escapam de `run_pending` e deixam o pedido em `running`. Exemplos: `_load_tokens` levantando `OAuthError` na linha 1155, `found["clip"]`, `update_request`.
  - O mesmo acontece com kill, OOM ou `SystemExit(75)`.
  - Como `running` está em `_ACTIVE_REQUEST_STATUSES`, `enqueue_request` recusa novos pedidos para o clipe, e `active_clip_ids` bloqueia a limpeza.
- **Correção:**
  - Envolver o corpo do laço em `except Exception` que chame `finish("erro", ...)`.
  - Gravar `iniciado_em`/`lease_until` no claim e, ao iniciar `run_pending`, devolver a `pending` os pedidos `running` vencidos.
  - Antes de repetir, checar `resultado.video_id` para não subir o vídeo duas vezes.

### A-02 Lives órfãs de um crash contam no `max_lives` para sempre
- **Domínio:** Filas/Concorrência
- **Local:** `clipes/acompanhar.py:616-624`, `painel/api/_nucleo.py:58-60`
- **Problema:**
  - O `worker_id` é persistente. Depois de `kill -9`, OOM ou queda de energia, os documentos `lives` ficam com `owner=<este PC>`, `status="live"` e a lease vencida.
  - `count_documents({"owner": wid, "status": "live", ...})` não filtra `lease_until`. Com `max_lives=1`, o PC recusa qualquer alvo até alguém limpar o Mongo à mão.
  - O painel mostra essas lives fantasmas como ativas.
- **Correção:**
  - Adicionar `"lease_until": {"$gte": now}` nos dois filtros.
  - Na inicialização, liberar as lives próprias com lease vencida.
  - Mover essa consulta para um método de `Coord`.

### A-03 Loop do `acompanhar` faz chamadas longas sem renovar a lease
- **Domínio:** Filas/Concorrência
- **Local:** `clipes/acompanhar.py:799-829` (o `_keepalive` só existe durante `_process_one`), `:844-895`, `:626` (`_wait_claim_sync` de até 30 s)
- **Problema:** fora do pipeline, `renew` só roda no topo do `while`. Numa mesma volta podem se somar:
  - `is_live_fn` (yt-dlp, de 5 a 30 s) por sessão;
  - `fechar_janela` (concat do ffmpeg + ffprobe) por janela atrasada;
  - `post_aviso` (HTTP de até cerca de 60 s, admitido no comentário da linha 800);
  - `_promote_aguardando` (probe, `_resolve_stream_id` e espera de claim).

  Com 2 ou 3 sessões, a volta passa fácil de 90 s, e acontece a mesma cascata do C-02.
- **Correção:** iniciar a thread de keepalive no começo de `run()` e pará-la no `finally`, em vez de só durante `_process_one`. Ou mover probes e avisos para threads.

### A-04 Discord e registro recebem todos os clipes da janela, sem o filtro de região dona e sem dedup
- **Domínio:** Arquitetura
- **Local:** `clipes/acompanhar.py:233-242`, `clipes/monitor.py:1005-1014`, `clipes/notificar.py:277-294`
- **Problema:** `LiveSession.pipeline` filtra `result["clips"]` com `owns`/`filter_emitted`, mas `notify_discord` relê o `clips.json` do `output_dir`, que contém todos os clipes. Como as janelas têm 600 s com passo de 300 s (50% de sobreposição), clipes duplicados e fora da região dona são postados, entram no `registro.json` e podem ser publicados.
- **Correção:** reescrever o `clips.json` com a lista filtrada dentro de `LiveSession.pipeline`, ou dar prioridade a `result["clips"]` em `notify_discord`.

### A-05 Pedidos de publicação nunca rodam no `acompanhar`, e os agendados não rodam no modo local
- **Domínio:** Arquitetura
- **Local:** `clipes/monitor.py:1511-1530` (único executor periódico), `clipes/bot.py:384-396`, `clipes/acompanhar.py` (não chama `run_pending`)
- **Problema:**
  - **Com Mongo:** um PC em `acompanhar`, `--follow` ou `--ate-acabar` envia heartbeat, então o bot responde "publicacao enfileirada". Mas os `publish_requests` com esse `owner` ficam `pending` para sempre.
  - **Sem Mongo:** `_maybe_run_local` só roda logo depois de um novo clique. Um pedido agendado (o padrão, nos horários 11:30/19:00) só sai quando alguém clicar em outro botão.
- **Correção:** um executor periódico de `run_pending` comum aos dois modos: thread no `Supervisor` e `discord.ext.tasks.loop` no bot em modo local.

### A-06 Cota diária do YouTube contada em UTC, não no horário do Pacífico
- **Domínio:** YouTube/Vídeo
- **Local:** `clipes/publicacao.py:39-40` (`_dt` em UTC), `:219` (`quota_ok`), `:233` (`_record_quota`)
- **Problema:**
  - A chave do dia é `_dt(ts).strftime("%Y-%m-%d")` em UTC, então o contador zera às 21:00 de Brasília.
  - A cota da YouTube Data API zera à meia-noite do Pacífico (07:00 ou 08:00 UTC).
  - O slot das 19:00 e os seguintes caem nessa janela. Com `CLIPES_YT_MAX_DIA=6` e cerca de 1600 unidades por upload, até 12 uploads podem cair no mesmo dia do Pacífico, e o 7º recebe 403 `quotaExceeded`.
- **Correção:** calcular o dia com `ZoneInfo("America/Los_Angeles")` em `quota_ok`, em `_record_quota` e na checagem de `request()` (`:787`).

### A-07 Bot faz I/O pesado e síncrono no event loop do gateway
- **Domínio:** Arquitetura
- **Local:** `clipes/bot.py:463`, `:564`, `:643` (`_request_publish`), `:370` (`find_clip`), `:670` (`_format_recent_clips`), `clipes/publicacao.py:576` (`root.rglob("clips.json")`)
- **Problema:** `publicacao.request` faz um `rglob` em todo o `out/`, lê os jobs de `done/`, usa flock e consulta o Mongo (com `serverSelectionTimeoutMS=5000`), tudo dentro do handler async e antes de `interaction.response.send_message`. Passando de 3 s, o Discord invalida a interação (erro 10062) e o heartbeat do gateway atrasa.
- **Correção:**
  - Chamar `await interaction.response.defer()` primeiro.
  - Rodar o trabalho com `await asyncio.to_thread(self._request_publish, ...)` e responder com `followup.send`. Fazer o mesmo para `find_clip` e `_format_recent_clips`.
  - Indexar os clipes pelo `registro.json` em vez do `rglob`.

---

## Média

### M-01 Com o Mongo fora do ar, dois PCs gravam a mesma live e a sessão nunca reivindica a lease depois
- **Domínio:** Filas/Concorrência
- **Local:** `clipes/acompanhar.py:628-632`, `clipes/monitor.py:1326-1330`, `:1345-1349`, `:1445-1446`
- **Problema:** se o claim falha por erro de Mongo, a sessão nasce com `live_id=None` e nunca mais chama `claim_live`/`renew`. No monitor, `_claim_live` devolve `True` e `hot_targets = active`.
- **Correção:** guardar o `live_id` e tentar o claim de novo a cada beat. Se o claim falhar depois de reconectar, encerrar a sessão.

### M-02 Corrida no limite `max_lives` entre tasks de claim do monitor
- **Domínio:** Filas/Concorrência
- **Local:** `clipes/monitor.py:1337-1351`, com as tasks criadas em `:1438-1443`
- **Problema:** várias tasks `ensure_claimed` passam pela checagem `len(owned_lives) < ml` antes de qualquer `await claim_live` terminar. O PC acaba com mais lives do que o limite.
- **Correção:** reservar a vaga antes do `await` (um conjunto `reserving` ou um `asyncio.Lock` em volta de checagem + claim).

### M-03 Comando `liberar` é ignorado no `acompanhar` e não expira
- **Domínio:** Arquitetura
- **Local:** `clipes/monitor.py:1497` (único consumidor), `painel/api/_nucleo.py:67-91`, `clipes/coord.py:32-36`
- **Problema:**
  - Num PC em `acompanhar`, o comando fica `pending` para sempre, e o índice único parcial faz o painel responder 409 em todas as tentativas seguintes.
  - Um comando criado para um worker offline dispara dias depois, assim que o worker sobe.
- **Correção:**
  - Extrair o tratamento de beat e comandos para uma função comum usada pelo `Supervisor`.
  - Gravar `expira_em` no comando.
  - No painel, recusar comando para worker sem heartbeat recente.

### M-04 `liberar` descarta o job em andamento e solta o lock antes de a thread do worker terminar
- **Domínio:** Filas/Concorrência
- **Local:** `clipes/monitor.py:1503-1510`, `:1566-1567`, `:969-974`
- **Problema:**
  - `child_killer` mata o ffmpeg e o llama-server. O pipeline falha e `run_worker_step` marca o job `rejected/pipeline_error`, em vez de devolvê-lo a `pending`.
  - `lock.close()` roda antes do `to_thread` terminar, e o ASR em processo continua rodando. O mesmo acontece em qualquer encerramento, não só no `liberar`: o `finally` de `run_monitor` (`clipes/monitor.py:1558-1567`) chama `pool.stop_all()` e `lock.close()` sem esperar a thread do `worker_loop`.
- **Correção:**
  - Usar um `threading.Event` de cancelamento.
  - Quando o stop for intencional, devolver o job a `pending`.
  - Fechar o lock só depois de juntar a thread do worker.

### M-05 Crash entre `complete()` e o move reprocessa um job já concluído
- **Domínio:** Filas/Concorrência
- **Local:** `clipes/monitor.py:743-750`, `:752-763`
- **Problema:** `complete` grava `status: done`, apaga o `source.mkv` e só depois faz `os.replace`. Se o processo morre nesse intervalo, `recover_orphans` força `pending`. O job é reprocessado sem fonte e vira `rejected`, sobrescrevendo o resultado bom. Além disso, o `finish_job` no Mongo nunca acontece.
- **Correção:**
  - Em `recover_orphans`, quando o status já for final, concluir o move para o estado correspondente.
  - Apagar o `source.mkv` só depois do move.

### M-06 Espelho do registro no Mongo pode guardar uma versão antiga marcada como sincronizada
- **Domínio:** Filas/Concorrência
- **Local:** `clipes/registro.py:209-249`
- **Problema:** o `replace_one` roda fora do lock. Se o processo A envia o snapshot v1 depois de o processo B ter enviado v2 e marcado o registro como sincronizado, o Mongo fica com v1 e o arquivo local diz `mongo_synced: True`.
- **Correção:** um campo `rev` incremental gravado sob o lock, usado no filtro: `replace_one({"_id": cid, "rev": {"$lt": rev}}, ...)`.

### M-07 `acompanhar` processa jobs alheios da fila sem o filtro de janela
- **Domínio:** Filas/Concorrência
- **Local:** `clipes/acompanhar.py:702-726`, `clipes/monitor.py:715-741`
- **Problema:** `_has_own_pending` só verifica se existe algum job próprio, mas `claim_next` pega o melhor job global. Sobras de execuções anteriores, ou jobs de pico do monitor (z > 0), caem em `pipeline_fn` puro, sem `owns`/`filter_emitted`.
- **Correção:** um predicado por `session_key` em `claim_next`, ou tratar as sobras explicitamente na inicialização.

### M-08 Buracos entre segmentos não são detectados e deslocam timestamps e chat
- **Domínio:** YouTube/Vídeo
- **Local:** `clipes/monitor.py:548-576`, `clipes/acompanhar.py:236-241`, `:269-271`
- **Problema:** `_select` não verifica se os segmentos são contínuos, e o concat gera um vídeo sem o buraco. Mesmo assim, `video_start + c["start"]` é tratado como tempo absoluto, o que quebra `owns`, `filter_emitted` e o alinhamento do chat.
- **Correção:** detectar buracos (`s[i+1] - s[i] > SEGMENT_S`) e gravar um mapa segmento → epoch, ou gerar um source por trecho contínuo.

### M-09 Teto global de buffer de 1,5 GB, sem hold no início da sessão
- **Domínio:** YouTube/Vídeo
- **Local:** `clipes/monitor.py:1142`, `:523-540`, `clipes/acompanhar.py:212-219`, `:316`, `:904`
- **Problema:** um YouTube em 1080p (de 0,5 a 1 MB/s) com 1200 s de buffer já chega perto do teto. O hold só passa a existir depois da primeira janela, então `enforce_cap` pode apagar os segmentos da primeira janela de uma sessão nova (`no_segments`/`too_short`).
- **Correção:** chamar `hold(key, t0)` em `start()`, dimensionar o teto pelo número de sessões (ou torná-lo configurável) e considerar limitar a 720p.

### M-10 URL de alvo `@handle/live` não é fixada no vídeo depois do probe
- **Domínio:** YouTube/Vídeo
- **Local:** `clipes/acompanhar.py:98-104`, `:545-547`, `:780`, `:356`
- **Problema:** `_probe_youtube` troca `chave` para `yt_<video_id>`, mas `alvo.url` continua `@handle/live`. Gravador, `is_live` e `meta.json` passam a seguir a live seguinte do canal com o `live_id` e o `emitted` da anterior.
- **Correção:** depois do probe, definir `alvo.url = f"https://www.youtube.com/watch?v={video_id}"`.

### M-11 `adiado` por cota é um estado terminal, e a checagem na hora do pedido não funciona
- **Domínio:** YouTube/Vídeo
- **Local:** `clipes/publicacao.py:787-802`, `:1119-1121`
- **Problema:** `quota_ok` só conta publicações concluídas, então N pedidos para o mesmo dia passam todos na hora do pedido. Os que excedem a cota viram `adiado` em `run_pending` e nunca são reagendados.
- **Correção:** contar também os pedidos `pending`/`running` do dia, e devolver o pedido a `pending` com o próximo slot em vez de marcá-lo `adiado`.

### M-12 Contador de cota por máquina, e não por conta do Google
- **Domínio:** YouTube/Vídeo
- **Local:** `clipes/publicacao.py:186-193`, `:226-242`
- **Problema:** `.cotas_publicacao.json` é local de cada PC. Dois PCs publicando na mesma conta somam os limites.
- **Correção:** com Mongo ativo, guardar o contador lá, com um `$inc` atômico e filtro `$lt limite`.

### M-13 Upload para o YouTube sem retry nem backoff
- **Domínio:** YouTube/Vídeo
- **Local:** `clipes/publicar_youtube.py:21` (`MAX_RESUMES = 3`), `:179-192` (init), `:199-226` (PUT)
- **Problema:** no init, qualquer 5xx, 429 ou erro de rede vira `erro` terminal. Nos resumes não há `sleep`, e `rateLimitExceeded`/`backendError` não são tratados como transitórios.
- **Mesmo problema no TikTok:** o PUT de `clipes/publicar_tiktok.py:159-170` também não tem retry nem backoff.
- **Correção:** backoff exponencial com jitter (de 1 a 60 s), de 5 a 8 tentativas, e tratar 429 e 403 `rateLimitExceeded` como retentáveis.

### M-14 `quotaExceeded` não atualiza o contador local
- **Domínio:** YouTube/Vídeo
- **Local:** `clipes/publicar_youtube.py:217-220`, `clipes/publicacao.py:1267-1283`
- **Problema:** depois de um 403 `quotaExceeded`, cada pedido seguinte sobe o vídeo inteiro de novo e falha. Além disso, uma falha em `_record_quota` vira só um `print`, e o upload não é contado.
- **Correção:** ao detectar `quotaExceeded`/`uploadLimitExceeded`, marcar o dia (do Pacífico) como esgotado. Tratar a falha de `_record_quota` como erro registrado.

### M-15 Terceira tentativa de `download_clip_window` perde a precisão do corte, e um timeout derruba o clipe
- **Domínio:** YouTube/Vídeo
- **Local:** `clipes/render.py:708-715`, `:724`
- **Problema:**
  - A tentativa 3 não usa `--force-keyframes-at-cuts`, então o vídeo começa no keyframe anterior e legendas e gancho ficam deslocados.
  - `subprocess.run(..., timeout=600)` não captura `TimeoutExpired`, o que interrompe as tentativas restantes.
- **Correção:** manter `--force-keyframes-at-cuts` (ou medir `start_time` com ffprobe e compensar) e capturar `TimeoutExpired` como falha da tentativa.

### M-16 `--max-dur` da live não chega ao pipeline
- **Domínio:** Arquitetura
- **Local:** `clipes/acompanhar.py:195`, `clipes/monitor.py:782`, `clipes/cli.py:48`
- **Problema:** `pad = max_dur/2` usa o valor do `acompanhar` (60), mas `run_local_pipeline` monta os argumentos sem `--max-dur`, e o CLI usa o padrão 65.
- **Correção:** gravar `max_dur`/`min_dur` no `job.json` e repassá-los em `run_local_pipeline`.

### M-17 Painel lê um banco Mongo diferente do dos workers
- **Domínio:** Arquitetura
- **Local:** `painel/api/_nucleo.py:175` vs `clipes/monitor.py:861`
- **Problema:** o painel usa `client[PAINEL_MONGO_DB or "clipes_lives"]`, e os workers usam `get_default_database(default="clipes_lives")`. Se a URI trouxer o nome do banco, o painel fica vazio e os comandos vão para o banco errado.
- **Correção:** usar `get_default_database(default=...)` também no painel.

### M-18 `/api/status` responde sem autenticação
- **Domínio:** Arquitetura
- **Local:** `painel/api/_nucleo.py:149`
- **Problema:** expõe `worker_id` (o mesmo identificador usado em `/api/comando`), aliases, canais, CPU/temperatura e jobs. A spec e o README falam em Basic Auth.
- **Correção:** exigir autenticação também no status. Atenção: `painel/index.html:437` faz `fetch("/api/status")` sem cabeçalho `Authorization` e sem tratar 401. O frontend precisa enviar o `basicHeader` no mesmo commit, senão o painel quebra.

### M-19 `policy.json` resolvido de quatro formas
- **Domínio:** Arquitetura
- **Local:** `clipes/monitor.py:1596`, `clipes/acompanhar.py:954`, `clipes/publicacao.py:659-666`, `clipes/cli.py:590-592`
- **Problema:**
  - `monitor`: ao lado de `channels.txt`;
  - `acompanhar`: `Path("policy.json")`, relativo ao cwd;
  - bot/publicação: `out_root/policy.json`;
  - `cli`: `CLIPES_POLICY_FILE`.

  Um bloqueio ou `delay_h` que vale no worker pode não valer na publicação.
- **Correção:** uma função única `resolve_policy_path()` (env, depois `OUT_DIR/policy.json`) usada por todos.

### M-20 Embargo `delay_h` pode ser contornado: `find_clip` ignora o `ready_at` do registro
- **Domínio:** Arquitetura
- **Local:** `clipes/publicacao.py:610-615`, `:669-677`
- **Problema:** no ramo `clips.json`, `ready_at`/`created_at` vêm só da entrada, do job ou do manifest. O manifest do CLI não tem esses campos, e o job só é procurado em `out/queue/done`. Assim `_effective_ready_at` devolve `None` e a publicação é aceita antes do prazo.
- **Correção:** usar `reg_rec["ready_at"]`/`reg_rec["criado_em"]` como fallback.

### M-21 Variáveis do `.env` que não chegam ao contêiner
- **Domínio:** Arquitetura
- **Local:** `docker-compose.yml:9-59` (serviço `clipes`), `:73-100` (serviço `bot`)
- **Problema:** o compose não tem `env_file` e só repassa as variáveis listadas. Ficam de fora, sem nenhum aviso:

  | Variável | Onde é lida |
  |---|---|
  | `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET` | `clipes/monitor.py:145-146`; estão em `.env.example:22-23` |
  | `CLIPES_BLUR_BG` | `clipes/render.py:739`; documentada no README |
  | `CLIPES_QUOTA_PATH` | `clipes/publicacao.py:189`; falta nos dois serviços |
  | `CLIPES_FACECAM_CONFIG` | `clipes/facecam.py:99` |
  | `CLIPES_LLM_TIMEOUT` | `clipes/llm.py:281` |
  | `CLIPES_CLOUD_TIMEOUT` | `clipes/cloud_llm.py:65` |
  | `CLIPES_GEMINI_COTA_FILE` | `clipes/cloud_llm.py:79` |
  | `CLIPES_MIN_FREE_GB` | `clipes/acompanhar.py:196`; também falta no `.env.example` |
  | `CLIPES_EMBED_ONNX`, `CLIPES_LLM_GGUF`, `CLIPES_YUNET_ONNX` | sem repasse no compose |
  | `CLIPES_POLICY_FILE` | falta só no serviço `bot` |
- **Correção:** adicionar as linhas ao `environment` (ou usar `env_file: .env`) e criar um teste que compare as variáveis lidas no código com as repassadas.

### M-22 Variáveis numéricas convertidas sem tratamento de erro, e contrato de heartbeat duplicado
- **Domínio:** Sintaxe/Tipagem
- **Local:** `painel/api/_nucleo.py:11` (roda na importação), `:53`, `clipes/monitor.py:862`, `:1274`, `clipes/acompanhar.py:196`, `:796`
- **Problema:**
  - Um valor como `CLIPES_BEAT_S=20s` derruba o painel inteiro já na importação.
  - `CLIPES_LEASE_S` inválido escapa do `except` de `connect` e derruba o worker.
  - O painel calcula `online` com o `BEAT_S` da Vercel, que é independente do `BEAT_S` do worker.
- **Correção:**
  - Um helper `env_float`/`env_int` com fallback e log.
  - O worker gravar `beat_s` no documento `workers`, e o painel usar `3 * w["beat_s"]`.

---

## Baixa

| ID | Domínio | Arquivo:linha | Problema | Sugestão |
|---|---|---|---|---|
| B-01 | Filas/Concorrência | `clipes/monitor.py:930`; `clipes/acompanhar.py:304-311` | Jobs criados com o Mongo offline (`coord_synced=False`) nunca entram no Mongo, e jobs sincronizados processados offline ficam `pending` lá para sempre | Replay idempotente (`add_job` + `finish_job`) depois de reconectar |
| B-02 | Filas/Concorrência | `clipes/monitor.py:688-691` | `_write_job` não faz fsync. Depois de uma queda de energia, um `job.json` vazio faz o job ir para `rejected` | `flush` + `os.fsync` no tmp antes do `replace` |
| B-03 | Sintaxe/Tipagem | `clipes/registro.py:6`, `:64-68`; `clipes/monitor.py:1149` | `import fcntl` sem fallback: `bot`, `publicacao` e `monitor` não importam no Windows nativo (20 arquivos de teste falham na coleta) | Import condicional (`msvcrt`/`portalocker`) ou mensagem clara; documentar que só roda em Linux/Docker |
| B-04 | Filas/Concorrência | `clipes/capacidade.py:177-193`; `clipes/coord.py:51` | `worker_id` é por `OUT_DIR`: duas instâncias com `--queue-dir` diferentes compartilham o dono da lease. A criação do arquivo não é atômica (`O_EXCL`) | `os.open(O_CREAT\|O_EXCL)` e um id de instância na lease |
| B-05 | YouTube/Vídeo | `clipes/ingest.py:167-190` | `record_live_stream`: `TimeoutExpired` sem captura; aceita arquivo curto (só checa `size > 10 KB`); o fallback pode reaproveitar um `live_source.*` antigo | Capturar o timeout, validar a duração, limpar resíduos e usar `-map` explícito |
| B-06 | YouTube/Vídeo | `clipes/publicacao.py:849-852`; `clipes/oauth.py:180-186`, `:225-232` | `_default_refresh` devolve só `access_token`. Um refresh token rotacionado (TikTok) fica velho em memória, e o read-modify-write de `tokens.json` não tem lock | Devolver também o `refresh_token` e usar `_locked_path` |
| B-07 | YouTube/Vídeo | `clipes/monitor.py:424`, `:427`, `:372` | stderr do yt-dlp, streamlink e ffmpeg vai para `DEVNULL` (e o yt-dlp roda com `-q`): falhas de captura não deixam rastro | Gravar o stderr num log rotacionado e registrar o `returncode` |
| B-08 | YouTube/Vídeo | `clipes/monitor.py:440-447` | `_stop` envia `terminate` à fonte e ao ffmpeg ao mesmo tempo, contrariando o comentário; o último segmento (usado em `encerrar`) pode ficar truncado | Terminar a fonte, esperar e só depois esperar ou terminar o ffmpeg |
| B-09 | Sintaxe/Tipagem | `clipes/monitor.py:22`, `:29-33`; `clipes/publicacao.py:5`, `:17`; `clipes/signals.py:8`; `clipes/notificar.py:670` | 16 imports não usados (F401) e a variável morta `supports_components` (F841) | Remover |
| B-10 | Arquitetura | `clipes/acompanhar.py:335-343`, `:614-620`; `clipes/monitor.py:684-691` vs `clipes/notificar.py:25-32`; `clipes/monitor.py:796-823` vs `clipes/registro.py:28-45` | Acesso a internals (`c.db`, `c._dt`, cerca de 10 `monitor._*`) e helpers duplicados (`_read_job`, `_is_mongo_error`, `_mask_uri`, `_local_worker_id`) | Criar `clipes/fila.py` e `clipes/mongo_util.py` com API pública e métodos novos em `Coord` |
| B-11 | Arquitetura | `clipes/monitor.py:773-788`; `clipes/publicacao.py:16` | Ciclo `monitor`↔`cli` com monkeypatch global de `cli.ingest`, que não é seguro entre threads; o bot importa `monitor`/numpy só para usar `ChannelPolicy` | Passar o ingest por parâmetro e mover `ChannelPolicy` para `clipes/policy.py` |
| B-12 | Arquitetura | `clipes/render.py:160-164`, `:326-327`, `:739-740`; `clipes/notificar.py:92-93`; `clipes/cloud_llm.py:43` | Flags booleanas com três estilos de parse diferentes | Um helper `env_flag(nome, padrão)` |
| B-13 | Arquitetura | `clipes/bot.py:136`; `clipes/publicacao.py:192`, `:1023`; `clipes/cloud_llm.py:82` | `OUT_DIR` sem `expanduser().resolve()`, ao contrário do `cli` e do `monitor` | Uma função única `resolve_out_dir()` |
| B-14 | Arquitetura | `clipes/render.py:57-115` vs `clipes/layout.py:55-104` | Geometria `split-*` duplicada com valores divergentes (`name_y` 52 vs 70, 36 vs 62) | Uma tabela única, ou documentar a separação |
| B-15 | Sintaxe/Tipagem | `painel/api/_nucleo.py:22-213`; `clipes/capacidade.py:147`; `clipes/discord_rest.py:151`, `:197`; `clipes/layout.py:128` | Funções públicas sem tipos, `dict[str, Any]` usado como contrato, `fc: Any`, `json.loads` anotado como dict | TypedDict, `Protocol` e `Literal` |
| B-16 | Sintaxe/Tipagem | `clipes/registro.py:441-466`; `clipes/notificar.py:235-256` | `CLIPES_RETENCAO_H` inválido desliga a retenção sem nenhum log | Validar e avisar |
| B-17 | Sintaxe/Tipagem | `clipes/perfil.py:83-85` vs `clipes/acompanhar.py:150-157` | `CLIPES_MAX_LIVES` é lido de duas formas (`isdigit` vs `int`) | Uma função de parse única |
| B-18 | Arquitetura | `README.md:15`, `:63`; `.env.example:21`, `:33`; `README.md:7` | README desatualizado: manda usar o branch `onda-ab`, diz "limita a 3 CPUs" (o `clipar` calcula outro valor) e "sem chaves de API" (mas `GEMINI_API_KEYS` existe). O `.env.example` diz que Twitch "não está implementado" e omite cerca de 20 variáveis | Atualizar a documentação e gerar o `.env.example` a partir de uma lista única |
| B-19 | Arquitetura | `Dockerfile:11`, `:46-56` | `WITH_LLM_4B` baixa cerca de 2,5 GB que nunca são usados, porque `CLIPES_LLM_GGUF` está fixo no modelo 1.7B e o compose não passa `build.args` | Ligar o ARG ao `CLIPES_LLM_GGUF` ou remover a opção |
| B-20 | Arquitetura | `docker-compose.yml:5`, `:67`, `:109`; `clipar` | `UID`/`GID` não são exportados pelo `clipar`, então o contêiner roda sempre como 1000:1000 | Exportar `HOST_UID=$(id -u)`/`HOST_GID` |
| B-21 | Sintaxe/Tipagem | `pyproject.toml:10-15` | Dependências incompletas: `pymongo`, `discord.py`, `onnxruntime`, `opencv` e `streamlink` só aparecem no Dockerfile | Declarar `optional-dependencies` e fazer o Dockerfile usá-las |
| B-22 | Arquitetura | `painel/api/_nucleo.py:15`, `:94-124` | O rate limit de senha guarda as falhas em memória por instância serverless e usa a chave de `X-Real-IP`/`X-Forwarded-For`; na prática é fraco | Rate limit persistente (Mongo/KV) e um limite global por rota |
| B-23 | Arquitetura | `clipes/coord.py:98`, `:142`; `clipes/monitor.py:1504-1508`; `painel/api/_nucleo.py:81-83` | Status e campos misturam português e inglês no mesmo contrato (`pending`/`executando`/`feito`/`rejeitado`, `updated_at`/`criado_em`) | Um Enum por contrato, num só idioma |
| B-24 | Sintaxe/Tipagem | `tests/test_discord_rest.py:268`; `vigia-temp:46`, `:54`, `:60`; `clipar:68` | F821 (`Any` não importado) num teste; o `vigia-temp` e o `trap` do `clipar` dependem do nome da pasta como nome do projeto do compose | Importar `Any`; adicionar `name: clipes-lives` ao compose |
| B-25 | YouTube/Vídeo | `clipes/acompanhar.py:783-789` vs `clipes/monitor.py:1284` | `Supervisor.run()` não limpa `queue_root/.buffer` ao iniciar (o monitor limpa). Depois de um crash recente, segmentos `.ts` da execução anterior podem entrar no concat da primeira janela | `shutil.rmtree(queue_root / ".buffer")` antes de criar o pool, como no monitor |

---

## Falsos positivos descartados na revisão

- **"`CLIPES_BEAT_S` e `CLIPES_LEASE_S` ausentes do compose":** as duas estão no compose (serviço `clipes`).
- **"`mp4_path` `Optional` passado ao `invoke`" (`clipes/publicacao.py:1202`):** os ramos com `None` fazem `continue` antes.
- **"`_CoordSession | None` usado sem guarda" (`clipes/monitor.py:1329`, etc.):** todos os usos estão depois de checagens que garantem não-`None`. É só um limite de inferência do mypy.
- **"Bypass do rate limit por `X-Forwarded-For`":** na Vercel esse cabeçalho é reescrito pelo proxy. O ponto real (contador em memória) foi rebaixado para B-22.
- **"Bot renova leases":** não renova. O bloqueio síncrono no bot afeta só a interação e o gateway (A-07); o atraso real de lease está no C-02 e no A-03.
- **"`cache_dir` inválido no `pyproject.toml`"** (apontado na revisão de fontes): `cache_dir` é uma opção válida de `[tool.pytest.ini_options]`. O aviso só apareceu porque o worker rodou com `-p no:cacheprovider`.
- **"`MongoQueue`":** essa classe não existe. A fila Mongo é implementada por `Coord` (`clipes/coord.py`) junto com o `DiskQueue` local.

---

## Status de implementação

Branch `fix/auditoria-inconsistencias` (a partir de `master` `de8e66a`), commits locais por onda:
onda 1 `ff6815f`, onda 2 `f1315eb`, onda 3 `298d751`, onda 4 `27752cc`, onda 5 `74fa87e`.
Suíte (Docker): 510 passed / 1 skipped antes → 761 passed / 1 skipped depois (o `test_real_llama_server_enrich_clips_smoke` falha às vezes por timeout do llama-server sob carga de CPU; falha igual no `master`). ruff F: 21 → 0. mypy (`--platform linux`): 29 → 28.

| Item | Status | Onda/commit | Observação |
|---|---|---|---|
| C-01 | corrigido | 2 `f1315eb` | restart do gravador com backoff; `ended` só com `is_live=False`; saída de segurança `CLIPES_LIVE_CAPTURA_MORTA_S` sem `ended` |
| C-02 | corrigido | 1 `ff6815f` | `PublishLoop` fora do heartbeat; lease ≥ 3×beat; `run_pending(limit=)` |
| A-01 | corrigido | 1 `ff6815f` | lease/`iniciado_em` no pedido, `except` por pedido, retomada sem reenvio |
| A-02 | corrigido | 1 `ff6815f` | `count_owned_live`/`release_expired_own` (só na 1ª conexão); painel filtra `lease_until` |
| A-03 | corrigido | 1 `ff6815f` | keepalive durante todo o `Supervisor.run` |
| A-04 | corrigido | 3 `298d751` | `clips.json` reescrito com a lista filtrada; `notify_discord` prioriza `result["clips"]` |
| A-05 | corrigido | 3 `298d751` | `PublishLoop` no `acompanhar` e no bot em modo local |
| A-06 | corrigido | 3 `298d751` | dia de cota do YouTube em `America/Los_Angeles` (TikTok segue UTC) |
| A-07 | corrigido | 4 `27752cc`, 5 `74fa87e` | defer + `to_thread` + followup; `find_clip` consulta o registro antes do `rglob` |
| M-01 | corrigido | 1, 4 | claim refeito após reconexão (monitor e acompanhar) |
| M-02 | corrigido | 1 | reserva da vaga antes do `await` |
| M-03 | corrigido | 1 | `Coord.run_pending_command` comum; `expira_em`; painel recusa worker offline |
| M-04 | corrigido | 1, 2, 4, 5 | cancel devolve o job a `pending`; lock só após join; pool congelado no `liberar` |
| M-05 | corrigido | 1 | `source.mkv` apagado após o move; `recover_orphans` conclui o move |
| M-06 | corrigido | 1 | `rev` incremental no espelho do registro |
| M-07 | corrigido | 1 | jobs sem sessão deste Supervisor não caem no pipeline cru |
| M-08 | corrigido | 2 | conservador: usa só o trecho contínuo mais recente (descarta segmentos antes do buraco) |
| M-09 | corrigido | 2 | hold no início da sessão; `CLIPES_BUFFER_CAP_GB`. 720p não adotado (decisão de produto) |
| M-10 | corrigido | 2 | `alvo.url` fixada em `watch?v=<id>` |
| M-11 | corrigido | 3 | conta pending/running; cota cheia reagenda (não cria mais `adiado`; legados mantidos) |
| M-12 | corrigido | 3 | com Mongo: reserva atômica (`$inc` + `$lt`) e estorno; sem Mongo continua por máquina |
| M-13 | corrigido | 3 | backoff com jitter no init/PUT (YouTube e TikTok) |
| M-14 | corrigido | 3 | `quota_esgotada` marca o dia; falha de `_record_quota` vira `aviso_cota` |
| M-15 | corrigido | 2 | `--force-keyframes-at-cuts` na 3ª tentativa; `TimeoutExpired` capturado |
| M-16 | corrigido | 3 | `max_dur`/`min_dur` no `job.json` e repassados ao CLI |
| M-17 | corrigido | 4 | `get_default_database` no painel |
| M-18 | corrigido | 4 | `/api/status` com Basic Auth; `index.html` envia o header |
| M-19 | corrigido | 4 | `config.resolve_policy_path` (env → `OUT_DIR/policy.json` → legado com aviso) |
| M-20 | corrigido | 3 | `ready_at`/`criado_em` do registro como fallback |
| M-21 | corrigido | 4 | variáveis repassadas no compose + teste de cobertura |
| M-22 | corrigido | 4 | `env_float`/`env_int`; worker grava `beat_s`, painel usa `3×beat_s` |
| B-01 | corrigido | 1, 2, 4 | `replay_unsynced_jobs` na conexão, após claim e periódico |
| B-02 | corrigido | 1 | `fsync` no `_write_job` |
| B-03 | parcial | 5 | import condicional com erro claro; sem lock de Windows (documentado: só Linux/Docker) |
| B-04 | parcial | 1 | `O_EXCL`; id de instância na lease adiado (mudaria o contrato de `owner`) |
| B-05 | corrigido | 2 | | 
| B-06 | corrigido | 3 | `refresh_token` rotacionado persistido sob lock |
| B-07 | corrigido | 2 | stderr em log por canal com rotação |
| B-08 | corrigido | 2 | fonte primeiro, depois ffmpeg |
| B-09 | corrigido | 5 | |
| B-10 | adiado | — | refatoração grande (`fila.py`/`mongo_util.py`) |
| B-11 | adiado | — | refatoração grande (ciclo monitor↔cli, `policy.py`) |
| B-12 | corrigido | 4 | `env_flag` |
| B-13 | corrigido | 4 | `resolve_out_dir` |
| B-14 | adiado (documentado) | 5 | tabelas não unificadas para não mudar o visual |
| B-15 | parcial | 4 | painel tipado; resto adiado |
| B-16 | corrigido | 4 | aviso para `CLIPES_RETENCAO_H` inválido |
| B-17 | corrigido | 4 | `env_int` único |
| B-18 | corrigido | 4 | README/.env.example |
| B-19 | corrigido | 4 | `WITH_LLM_4B` aponta o GGUF via symlink |
| B-20 | corrigido | 4 | `HOST_UID`/`HOST_GID` |
| B-21 | parcial | 4 | `optional-dependencies` declaradas; Dockerfile não instala via pyproject |
| B-22 | adiado | — | exige KV/Mongo persistente na Vercel |
| B-23 | adiado | — | mudança de contrato (status/campos) |
| B-24 | corrigido | 4, 5 | `name: clipes-lives`; `Any` importado |
| B-25 | corrigido | 2 | |
