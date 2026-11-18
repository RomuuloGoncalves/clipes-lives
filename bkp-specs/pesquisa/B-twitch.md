# Pesquisa B: Twitch (API, monitor de categoria, gravação, direitos)

Data da pesquisa: 2026-11-17. Somente leitura + web; nenhum código do projeto foi alterado.
Convenção: cada fato traz [fonte, data de consulta]. "NÃO VERIFICADO" = não achei fonte oficial legível.
Obs.: legal.twitch.com (ToS, DSA, Music) devolveu só navegação ao fetch (página renderizada por JS), então o texto legal não foi lido.

## 1. Listar lives de uma categoria (Helix Get Streams)
- `GET https://api.twitch.tv/helix/streams?game_id=<id>&first=100`. Aceita user token OU app access token, sem escopos. [dev.twitch.tv/docs/api/reference/streams, 2026-11-17]
- `game_id` aceita até 100 ids; `first` 1–100 (padrão 20); paginação por cursor `after`; filtros `language`, `user_id`, `user_login`. [idem]
- Ordem: decrescente por `viewer_count`. Paginar pode duplicar/pular streams, pois os viewers mudam. Pode vir página com menos de `first` itens mesmo havendo mais. [idem]
- Campos úteis: `user_id`, `user_login`, `viewer_count`, `started_at`, `language`, `title`, `game_id`. [idem]
- Para achar o `game_id` pelo nome: endpoints Games/Search categories existem no índice da API. [dev.twitch.tv/docs/api/reference, 2026-11-17] (parâmetros exatos: não verificado)
- Auth: app token via client credentials, `POST https://id.twitch.tv/oauth2/token` (client_id, client_secret, grant_type=client_credentials). Sem refresh token; exemplo de `expires_in` ≈ 5.011.271 s (~58 dias). Exige registrar um app. [dev.twitch.tv/docs/authentication/getting-tokens-oauth, 2026-11-17]
- É gratuito? A página não diz. NÃO VERIFICADO (na prática o registro de app é gratuito, mas não achei fonte nesta rodada). Exige client_id/secret: segredo fica em env var, nunca em arquivo.
- Rate limit: token bucket por client ID, buckets separados para app token e user token; a maioria dos endpoints custa 1 ponto; estourou = HTTP 429; headers `Ratelimit-Limit`, `Ratelimit-Remaining`, `Ratelimit-Reset` (epoch). [dev.twitch.tv/docs/api/guide, 2026-11-17] Tamanho do bucket (ex.: 800/min): NÃO VERIFICADO.
- Implicação: varrer uma categoria grande = ~1 req por 100 streams; polling a cada 60–120 s é barato. Streams de baixa audiência ficam no fim da paginação (ordem por viewers).
- Alternativa p/ detecção de entrada/saída: EventSub `stream.online`/`stream.offline` (custo 0 se o broadcaster autorizou o app; senão consome cost). [dev.twitch.tv/docs/eventsub/manage-subscriptions, 2026-11-17] Para um monitor simples, polling de Get Streams basta.

## 2. Chat: IRC anônimo vs EventSub
- IRC: a doc oficial só descreve login com user token (`chat:read`/`chat:edit`). `justinfan` anônimo NÃO aparece na página consultada. [dev.twitch.tv/docs/chat/irc, 2026-11-17] → anônimo somente-leitura: NÃO VERIFICADO na doc oficial (é convenção conhecida da comunidade; testar antes de depender).
- A doc recomenda EventSub + API no lugar de IRC ("IRC tem limitações versus EventSub"), sem declarar IRC descontinuado. [idem]
- JOIN: 20 joins e 20 tentativas de auth (só IRC) por 10 s por usuário; bots verificados 2.000/200. [dev.twitch.tv/docs/chat, 2026-11-17]
- Limite de salas simultâneas: 100 chat rooms por conta de usuário (desde 15/05/2024); não conta se o broadcaster/moderador ou se o broadcaster autorizou `channel:bot` com app token. Os limites de join não se aplicam a subs `channel.chat.message` feitas com app token. [idem]
- EventSub WebSocket: máx. 3 conexões por (client ID + user), 300 subs habilitadas por conexão, `max_total_cost` = 10 no WebSocket (a mesma página mostra exemplo de 10000 em outro contexto). WebSocket exige user token; webhook exige app token. [dev.twitch.tv/docs/eventsub/manage-subscriptions, 2026-11-17]
- Escopo exato de `channel.chat.message` (user:read:chat etc.): NÃO VERIFICADO (a página de tipos foi movida para /docs/eventsub/reference/ e o fetch não retornou o conteúdo).
- Conclusão prática (inferência minha, não da fonte): sem token de usuário, o caminho mais simples é IRC anônimo (justinfan) limitado a ≤100 canais e ≤20 JOINs/10 s com backoff. Com token de usuário, EventSub é o caminho oficial. Para o piso (6 GB/6 threads), ~50–100 canais num único socket asyncio é leve em CPU (só texto), mas isso é estimativa, NÃO medida.
- Pico relativo (já existe `clipes/signals.py::signal_chat`; leitura parcial: janelas, onda de copy-paste ≥3 autores/5 s, cap Poisson). Desenho sugerido: contador por canal em janelas de 10 s; baseline = mediana/EMA das últimas ~5–10 min (excluindo o pico atual); pico quando `taxa10s ≥ max(min_abs, k·baseline)` e z-score Poisson alto; `min_abs` pequeno (ex.: 4–6) para evitar "0→3 msgs" em canais minúsculos; exigir ≥N autores distintos; cooldown por canal (ex.: 2–3 min).
- Anti-bot/flood: descartar nicks de bots conhecidos (nightbot, streamelements, etc. — lista própria), mensagens começando com `!`, repetição do mesmo autor, mensagens idênticas em rajada (reusar a detecção de onda já existente), contar autores únicos em vez de mensagens brutas. Sem fonte oficial; é design.

## 3. Gravação
- streamlink: plugin Twitch filtra pré-roll/mid-roll automaticamente (a opção `--twitch-disable-ads` não é mais necessária); durante o anúncio a saída pausa, podendo haver descontinuidade. Anúncios podem ser evitados com autenticação (Turbo) via `--twitch-api-header=Authorization=OAuth <token>` (token = cookie `auth-token`, é segredo com acesso total à conta). [streamlink.github.io/cli/plugins/twitch.html, 2026-11-17]
- streamlink: `--hls-live-edge` (padrão 3), `--stream-segment-threads` (1–10, padrão 1), `--retry-streams`, `--retry-max`, `--stream-timeout` (60 s), `--ringbuffer-size` (16M), `--stdout`. [streamlink.github.io/cli.html, 2026-11-17]
- streamlink | ffmpeg: `streamlink --stdout URL best | ffmpeg -i pipe:0 ...` é o padrão documentado no man. [mankier.com/1/streamlink, 2026-11-17]
- Cópia sem reencode (`-c copy`) evita CPU de transcodificação (artigo de engenharia da Twitch, sem números). [medium.com/twitch-news, 2026-11-17] Medição de CPU/disco: NÃO VERIFICADO — medir no piso.
- ffmpeg `segment` muxer tem opções `segment_time`, `segment_wrap`, `segment_list_size`, `segment_wrap_number`, `reset_timestamps`, `segment_format`, `segment_atclocktime` [ffmpeg.org/ffmpeg-formats.html, 2026-11-17]; semântica detalhada não lida (NÃO VERIFICADO). Alternativa documentada no muxer `hls`: `hls_list_size` + `delete_segments` apaga segmentos antigos. [ffmpeg.org / muxers.texi, 2026-11-17]
- yt-dlp para live Twitch: NÃO VERIFICADO nesta rodada (o projeto já o usa p/ ingest). Preferência sugerida: streamlink (filtra ads, retry, reconexão nativos).
- Buffer rolante (design): `streamlink --stdout | ffmpeg -c copy -f segment -segment_time 10 -segment_wrap N ... seg_%03d.ts`, N ≈ (pre-roll + pós + margem)/10. Ex.: 6 min de buffer → 36 segmentos. Ao detectar pico em T, copiar (hardlink/cópia) os segmentos de [T−pre, T+pos] para a pasta de "captura" e concatenar com `-c copy`; só depois entra no pipeline atual (ASR/snap/render). Pre-roll de 60–120 s cobre "assunto até ~3 min" parcialmente; para assunto longo, estender o buffer ou continuar gravando enquanto o chat estiver acima do baseline.
- Capacidade no piso (ESTIMATIVA, não medida): vídeo 1080p60 da Twitch ≈ 6–8 Mbps ⇒ ~0,8–1 MB/s por canal ⇒ 6 min de buffer ≈ 300–360 MB/canal em disco (ou tmpfs); 10 canais ≈ 3–4 GB. Banda: 10 canais ≈ 60–80 Mbps. Recomendo gravar a variante de menor qualidade suficiente (720p/480p) para o buffer de detecção, e só baixar a melhor qualidade do trecho depois (re-fetch via VOD) se o VOD estiver disponível. Realista no piso: ~4–8 gravações simultâneas em `-c copy` (CPU baixa, gargalo = disco/banda/RAM de page cache), com render/ASR sequencial fora disso. Validar com teste real.
- Evitar anúncios: filtro do streamlink (ver acima) cria buracos/descontinuidade — tratar com `reset_timestamps` e marcar o segmento; auth Turbo só se o usuário tiver conta (decisão do usuário; não registrar token em arquivo).

## 4. Create Clip API
- Requer user access token com escopo `clips:edit`, e o broadcaster precisa estar ao vivo e com clipes habilitados; pode haver exigência de follow/sub se o canal restringir. [dev.twitch.tv/docs/api/clips, 2026-11-17]
- Janela capturada: até 90 s (~85 s antes + 5 s depois); publica por padrão até os últimos 30 s; via `edit_url` (válida 24 h) dá para ajustar o trecho de 5 a 60 s e o título. `has_delay` (padrão false) desloca a janela. Criação é assíncrona: consultar Get Clips; se não aparecer em 15 s, considerar falha. [idem]
- Get Clips: qualquer token (app token serve); filtros broadcaster_id/game_id/id; campos `url`, `embed_url`, `creator_name`, `broadcaster_name`, `vod_offset`, `duration`, `view_count`. [idem]
- Rate limit específico do Create Clip: NÃO VERIFICADO. Create Clip From VOD: apareceu no índice da API ("get, create, download, create from VODs") mas sem detalhes lidos. [dev.twitch.tv/docs/api/reference, 2026-11-17]
- Uso como complemento: máx. 60 s, nasce curto/cortado (conflita com a decisão 5), mas dá (a) link oficial + `creator_name`/`broadcaster_name` para crédito, (b) `vod_offset` para localizar o ponto exato no VOD e refazer o corte longo, (c) sinal de qualidade (clips já criados por viewers = pico humano; Get Clips ordena por views). Recomendação: ler clipes existentes via Get Clips (app token, sem user token) como sinal extra; criar clipe só se houver user token do usuário (a decidir).

## 5. Direitos e ToS (NÃO é aconselhamento jurídico)
- Texto do ToS, Developer Services Agreement e política de música da Twitch: NÃO VERIFICADO (conteúdo não carregou). Falta ler: restrições a copiar/redistribuir conteúdo, e se o DSA proíbe armazenar/retransmitir vídeo obtido via API/streamlink.
- Excerto (busca, versão em espanhol do ToS, secundário): a licença de uso dos serviços é limitada, não sublicenciável, para uso pessoal/interno, com restrições a revenda, uso comercial e distribuição/exibição pública. [legal.twitch.com/es-mx/legal/terms-of-service/20210101, 2026-11-17, trecho de resultado de busca]
- Clipe não é licença livre: o direito do conteúdo é do streamer; republicar em outra plataforma pode exigir permissão específica (plataformas, edição, monetização, duração). [techbloat.com / usethirdchair.com, blogs terceiros, 2026-11-17 — fonte secundária]
- Twitch tem ferramentas oficiais para o próprio streamer exportar clipes ao YouTube Shorts/TikTok (Clip Editor). [tubefilter.com 2023-05-12, secundária] Isso vale para o dono do canal, não para terceiros.
- Música: a Twitch orienta não tocar música sem direitos; DMCA já derrubou clipes/VODs antigos em massa (2020). [blog.twitch.tv/en/2020/11/11/music-related-copyright-claims-and-twitch, 2026-11-17] Licença da Twitch não cobre repost em YouTube/TikTok/Instagram. [usethirdchair.com, secundária]
- Riscos práticos para o bot (inferência a partir das fontes acima): (1) strike/DMCA por música de fundo ou jogo/mídia de terceiros no clipe; (2) streamer pedir remoção; (3) violação de ToS/DSA ao redistribuir se não houver permissão; (4) risco maior ao PUBLICAR (próxima fase) do que ao detectar/gravar. Mitigações de design: crédito (canal + link da live), lista de canais com permissão (opt-in/allowlist) como modo seguro, detecção/filtro de música (a avaliar), manter registro de origem em `clips.json`, e baixa/retenção curta do buffer bruto (apagar segmentos após uso).

## 6. Desenho sugerido do "monitor" (pequeno, piso 6 GB/6 threads)
Processos (um container, 3 papéis):
1. `discover` (asyncio, 1 tarefa): a cada 60–120 s, Get Streams por `game_id` (filtro `language`, `viewer_count` mínimo), pagina até N canais; escolhe os top-K a vigiar (K ≤ ~20 no total), mantém `watchlist.json`.
2. `chat` (asyncio, 1 socket IRC anônimo ou EventSub): entra em ≤100 canais respeitando 20 JOINs/10 s com backoff; mantém por canal contagem 10 s + baseline EMA; ao detectar pico relativo publica evento `{canal, t_pico}` na fila.
3. `recorder` (pool de subprocessos): só para os canais com atividade (ex.: top-M por taxa de chat, M ≤ 4–6): `streamlink --stdout | ffmpeg -c copy -f segment -segment_wrap N`. Buffer rolante de ~6 min por canal em `/work/buf/<canal>/seg_%03d.ts` (tmpfs se couber).
Fila/estado em disco (sem banco): diretórios `queue/pending/`, `queue/processing/`, `queue/done/` com um JSON por evento (canal, link da live, t_pico, vod_offset se houver, segmentos congelados); `state/` com watchlist e baselines (JSON). Escrita atômica (rename).
Worker de processamento: 1 por vez (Whisper pesado sequencial, nunca junto de LLM): congela os segmentos [t−pre, t+pos], concatena com `-c copy`, entrega ao pipeline existente (sinais → ASR → snap → render), grava crédito (canal + URL da live).
Limites de concorrência: ≤ M gravações ativas; 1 job de ASR/render por vez; limpar buffer de canais sem pico há >10 min; teto de disco do buffer (ex.: 4 GB) com descarte do mais antigo; cooldown por canal; teto de eventos/hora.
Primeiro passo recomendado: medir CPU/disco/banda com 4 gravações `-c copy` no container real antes de fixar K e M.

## Pendências (NÃO VERIFICADO)
ToS/DSA/Music da Twitch (texto); tamanho do bucket do Helix; escopo de `channel.chat.message`; justinfan na doc oficial; rate limit do Create Clip; semântica de `segment_wrap`; yt-dlp vs streamlink em live Twitch; consumo real de CPU/disco.
