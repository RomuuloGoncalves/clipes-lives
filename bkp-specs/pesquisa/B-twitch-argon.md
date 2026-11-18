# Pesquisa B — Twitch: API Helix, Monitor de Categoria, Chat, Gravação em Buffer Rolante, Clips API e Direitos/ToS

- **Data da pesquisa:** 2026-11-17
- **Agente / Worktree:** `pesq-argon-B` (Gemini 4 Argon)
- **Escopo:** Levantamento técnico e crítico com fontes verificadas na web (priorizando `dev.twitch.tv`, `legal.twitch.com`, `streamlink.github.io` e `ffmpeg.org`) aplicado às restrições duras do projeto `clipes-lives` (**container Docker com 6 GB RAM, 6 threads no Ryzen 5 5500U, sem GPU, 100% gratuito, processamento pesado estritamente sequencial**).

---

## 1. Descoberta de Lives por Categoria (`Helix Get Streams` por `game_id`)

### 1.1. Endpoints Oficiais (`dev.twitch.tv`)

Para listar todas as transmissões ao vivo de uma categoria (ex.: *Just Chatting*, *Grand Theft Auto V*, *League of Legends*), o fluxo oficial usa dois endpoints da API Helix:

1. **Resolver nome da categoria para `game_id` (`Get Games`):**
   - **Endpoint:** `GET https://api.twitch.tv/helix/games?name=<nome_url_encoded>`
   - **Observação prática:** O `game_id` de cada categoria é estático e pode ser cacheado permanentemente em disco (ex.: *Just Chatting* = `509658`, *Grand Theft Auto V* = `32982`, *Counter-Strike* = `32399`, *Valorant* = `516575`).
   - **Fonte:** [Twitch API Reference — Get Games](https://dev.twitch.tv/docs/api/reference/#get-games) (verificado em 2026-11-17).

2. **Listar lives ativas da categoria (`Get Streams`):**
   - **Endpoint:** `GET https://api.twitch.tv/helix/streams`
   - **Parâmetros de query relevantes:**
     - `game_id`: string (aceita **até 100 IDs** na mesma chamada repetindo o parâmetro: `?game_id=509658&game_id=32982`).
     - `language`: código ISO 639-1 de 2 letras (ex.: `language=pt` para filtrar apenas lives em português; aceita até 100 idiomas).
     - `type`: `live` (padrão `all`, que inclui `live`; recomenda-se passar `type=live`).
     - `first`: inteiro de `1` a `100` (padrão `20`). Usar `first=100` minimiza o número de requisições.
     - `after`: cursor opaco (`pagination.cursor`) para paginar além dos top 100 canais.
   - **Ordenação e retorno:** Os resultados vêm ordenados por `viewer_count` decrescente. Cada item traz `id` (ID da stream), `user_id` (`broadcaster_id`), `user_login` (handle para IRC/Streamlink), `user_name`, `game_id`, `game_name`, `title`, `viewer_count`, `started_at` (ISO-8601 UTC), `language` e `is_mature`.
   - **Fonte:** [Twitch API Reference — Get Streams](https://dev.twitch.tv/docs/api/reference/#get-streams) (verificado em 2026-11-17).

### 1.2. Autenticação Necessária: App Access Token (É gratuito?)

- **Obrigatório?** Sim. A API Helix oficial rejeita requisições sem os cabeçalhos `Client-Id: <client_id>` e `Authorization: Bearer <token>` com `401 Unauthorized`.
- **Qual tipo de token?** Basta um **App Access Token** obtido via fluxo **OAuth Client Credentials Grant** (`server-to-server`, sem login interativo de usuário e **sem nenhum escopo/scope necessário**):
  ```sh
  curl -X POST "https://id.twitch.tv/oauth2/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "client_id=${TWITCH_CLIENT_ID}&client_secret=${TWITCH_CLIENT_SECRET}&grant_type=client_credentials"
  ```
- **Custo:** **100% gratuito.** Basta registrar uma aplicação no [Twitch Developer Console](https://dev.twitch.tv/console/apps) (exige apenas uma conta comum da Twitch com autenticação de dois fatores — 2FA — ativada). O token dura ~60 dias (`expires_in` ~5.000.000 s) e pode ser renovado automaticamente quando receber `401`.
- **Fonte:** [Twitch Docs — Getting OAuth Access Tokens (Client Credentials Grant Flow)](https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/#client-credentials-grant-flow) (verificado em 2026-11-17).

### 1.3. Rate Limits da Helix API

- **Algoritmo:** Token-bucket por `Client-Id`.
- **Quota com App Access Token:** **800 pontos por minuto** (capacidade do bucket = 800, recarga = 800 pontos/minuto).
- **Custo do `Get Streams`:** **1 ponto por requisição** (retornando até 100 lives por página).
- **Cabeçalhos de controle na resposta HTTP:**
  - `Ratelimit-Limit`: capacidade total (800).
  - `Ratelimit-Remaining`: pontos disponíveis no momento.
  - `Ratelimit-Reset`: timestamp Unix (segundos) em que o bucket volta a 800.
  - Se excedido, retorna `HTTP 429 Too Many Requests`.
- **Impacto prático para o monitor:** Consultar as top 100 lives em `language=pt` de uma categoria a cada 60 segundos consome **1 ponto/minuto** (0,125% da quota). Mesmo paginando 500 lives a cada 30 segundos, o consumo é de apenas 10 pontos/minuto.
- **Fonte:** [Twitch API — Rate Limits](https://dev.twitch.tv/docs/api/guide/#rate-limits) (verificado em 2026-11-17).

### 1.4. Nota Crítica: Helix Oficial vs GraphQL Interno (`gql.twitch.tv`)

Hoje, `clipes/ingest.py` (linhas 22–24) usa o endpoint não-oficial `https://gql.twitch.tv/gql` com o `Client-Id` público do player web (`kimne78kx3ncx6brgo4mv6wki5h1ko`) para baixar o replay de chat de VODs (`VideoCommentsByOffsetOrCursor`).
- **Pode-se listar lives de categoria sem chave via GQL ou `yt-dlp`?** Tecnicamente existe a query GQL `DirectoryPage_Game` (e `yt-dlp --flat-playlist "https://www.twitch.tv/directory/category/<slug>"`), mas a Twitch frequentemente altera hashes de *persisted queries* ou exige `Client-Integrity` (Kasada anti-bot) em queries de diretório.
- **Recomendação:** Para `Get Streams`, usar a **Helix API oficial com App Access Token gratuito** se o usuário aceitar configurar `TWITCH_CLIENT_ID` e `TWITCH_CLIENT_SECRET` no `.env` (zero custo financeiro, zero risco de quebra por hash GQL); manter fallback opcional para uma lista estática de canais (`channels.txt`) ou `yt-dlp` caso o container rode sem variáveis de ambiente.

---

## 2. Monitoramento de Chat em Tempo Real: IRC Anônimo (`justinfan`) vs EventSub

Atualmente, o modo live (`--live-minutes` em `clipes/ingest.py:420-425`) ignora o chat (`[ingest] Live mode: skipping replay chat fetch.`), degradando a detecção para `loudness` apenas (`docs/teste-real.md:78,105`). Para detectar picos em tempo real na Twitch, há duas interfaces:

### 2.1. Comparativo Técnico: IRC Anônimo (`justinfan`) vs EventSub (`channel.chat.message`)

| Critério | IRC Anônimo (`justinfan<N>`) | EventSub WebSocket (`channel.chat.message`) |
|---|---|---|
| **Protocolo / Endpoint** | TCP `irc.chat.twitch.tv:6667` (TLS `:6697`) ou WebSocket `wss://irc-ws.chat.twitch.tv:443` | WebSocket `wss://eventsub.wss.twitch.tv/ws` + chamadas REST `POST /helix/eventsub/subscriptions` |
| **Autenticação necessária** | **Nenhuma (Zero-Key / 100% anônimo):** `PASS schmoopie` + `NICK justinfan12345` | **Obrigatória:** exige **User Access Token** com escopo `user:read:chat` (ou App Token + `user:bot` + mod/`channel:bot` do streamer) |
| **Metadados de mensagem** | Completos via `CAP REQ :twitch.tv/tags twitch.tv/commands` (`user-id`, `mod`, `subscriber`, `emotes`, `tmi-sent-ts`) | Completos via payload JSON estruturado |
| **Taxa de `JOIN` / Assinatura** | **20 `JOIN`s a cada 10 segundos** por usuário/IP (2 joins/s) | 1 chamada HTTP `POST` por canal (consome quota Helix e exige `session_id` ativo) |
| **Limite de salas simultâneas** | **100 canais simultâneos** por conta/identidade padrão (ou ~50–100 canais por conexão WebSocket anônima sem throttling) | **Máximo de 100 canais simultâneos** por conta `user_id` não-moderadora (limite global Concurrent Join Limit da Twitch) e máx. 3 conexões WebSocket por token |
| **Overhead no container (RAM/CPU)** | **Mínimo:** 1 coroutine `asyncio` (`< 15 MB` RAM, `< 1%` CPU para 50–100 canais) | Médio: gerência de keepalive, recriação de até 100 subscriptions HTTP a cada reconexão de WebSocket |
| **Status na documentação** | Funcionalidade histórica do web player (suportada nos servidores TMI, embora a doc oficial promova OAuth/EventSub) | Oficialmente recomendado pela Twitch para bots autenticados |

**Fontes verificadas (2026-11-17):**
- [Twitch Docs — Chat & IRC Rate Limits](https://dev.twitch.tv/docs/irc/#rate-limits)
- [Twitch Docs — Connecting to Twitch IRC](https://dev.twitch.tv/docs/irc/authenticate-bot/)
- [Twitch Docs — EventSub Subscription Types: `channel.chat.message`](https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types/#channelchatmessage)
- [Twitch Docs — EventSub WebSocket Limits](https://dev.twitch.tv/docs/eventsub/handling-websocket-events/#limits)

### 2.2. Conclusão sobre Conexão de Chat e Limites de `JOIN`

Para o `clipes-lives`, o **IRC Anônimo (`justinfan`) sob `wss://irc-ws.chat.twitch.tv:443` (ou TCP `:6697`)** é nitidamente superior ao EventSub porque:
1. **Não exige login de usuário nem renovação de User Access Token** (cumpre a regra dura *"Tudo gratuito, sem chave de API"* do projeto).
2. **Limites exatos a respeitar no código do monitor IRC:**
   - **Rate limit de autenticação:** máx. **20 conexões/autenticações por 10 s** por IP.
   - **Rate limit de `JOIN`:** máx. **20 comandos `JOIN #canal` por 10 s** (na prática, espaçar **1 `JOIN` a cada 0,6 s** garante margem de segurança absoluta e entra em 50 canais em 30 segundos).
   - **Capacidade simultânea realista:** **50 a 100 canais simultâneos** em 1 ou 2 conexões WebSocket anônimas consomem menos de **10–30 KB/s** de banda de rede e `< 1%` de 1 thread de CPU.
   - **Tags IRCv3:** Enviar `CAP REQ :twitch.tv/tags twitch.tv/commands` logo após conectar permite ler `@user-id=...;tmi-sent-ts=...;mod=...;subscriber=...` em cada `PRIVMSG`, além de receber `CLEARCHAT` (mensagens apagadas/banidas por moderação) e `NOTICE` (`msg-id=emote_only_on`, etc.).

### 2.3. Como Medir Pico Relativo em Tempo Real (Taxa por 10 s vs Baseline por Canal)

A ideia original do usuário (*"6+ msgs em 10s"*) falha em dois extremos:
- Em um canal grande (ex.: 15.000 viewers, ~8 msgs/s = 80 msgs/10s), `6 msgs/10s` está **100% do tempo** ativo (falso positivo contínuo).
- Em um canal pequeno/médio (ex.: 150 viewers, ~0,2 msg/s = 2 msgs/10s), um salto repentino para `12 msgs/10s` com `"KKKKK"`, `"CLIPAAA"`, `"MEU DEUS"` é um pico legítimo de 6x a média.

O projeto já possui uma excelente modelagem offline em `clipes/signals.py::signal_chat` (linhas 150–230). Para o **monitor em tempo real**, basta converter o cálculo de `signal_chat` para uma **janela deslizante causal (online)** por canal, mantendo contadores de 1 Hz em um `collections.deque(maxlen=180)` (últimos 3 minutos):

1. **Contagem filtrada por segundo (`rate[t]` e `react[t]`):**
   - A cada segundo `t`, contabilizam-se as mensagens válidas (`rate[t]`) e quantas delas bateram nos padrões de reação de `signals.py:35-49` (`REACTION_WHOLE_RE` / `REACTION_CONTAINS_RE`: `k{3,}`, `lul`, `kekw`, `caralho`, `clipa`, `mds`, emojis, etc.) ou ondas de copy-paste (`react[t]`).
2. **Baseline causal por canal (`baseline_10s`):**
   - Calcula-se a **mediana móvel** da contagem em janelas de 10 s ao longo dos **últimos 120 a 180 segundos** (excluindo os últimos 15 s para que a própria subida do pico não infle o baseline).
   - Aplica-se um piso mínimo para canais muito silenciosos: `expected_10s = max(median_10s, mean_10s * 0.4, 2.5)`.
3. **Critério duplo de disparo (Razão Relativa + Z-score de Poisson + Diversidade de Autores):**
   Na janela atual de 10 segundos (`count_10s` mensagens, `react_10s` reações, `unique_authors_10s` usuários distintos):
   - $\text{ratio} = \frac{\text{count}_{10s}}{\text{expected}_{10s}}$
   - $z = \frac{\text{count}_{10s} - \text{expected}_{10s}}{\sqrt{\text{expected}_{10s} + 1.0}}$ (mesma fórmula de `clipes/signals.py:218`)
   - **Gatilho de pico (`fire`):**
     - `count_10s >= 6` (mantém o piso absoluto mínimo sugerido pelo usuário) **E**
     - `unique_authors_10s >= 4` (impede que 1 ou 2 spammers disparem sozinhos) **E**
     - `z >= 3.5` **E** (`ratio >= 3.0` **OU** (`ratio >= 2.2` e `react_10s / count_10s >= 0.45`)).
   - **Período de aquecimento (warm-up):** Ao entrar em um canal novo, aguardar **60 segundos** acumulando histórico antes de habilitar o disparo.

### 2.4. Como Evitar Bots, Sorteios e Flood no Chat

Aproveitando e expandindo o que já existe em `clipes/ingest.py:17-20` (`BOT_AUTHORS`) e `clipes/signals.py:26,162-197`:
1. **Blacklist de bots conhecidos + sufixo:** Descartar `BOT_AUTHORS` (`nightbot`, `streamelements`, `streamlabs`, `moobot`, `fossabot`, `botrix`, `wizebot`, `sery_bot`, `soundalerts`, `botrixoficial`, `pokemoncommunitygame`, `frostytoolsdotcom`, `creatisbot`) e logins terminados em `bot`.
2. **Cooldown por autor (`AUTHOR_COOLDOWN_S = 3.0s`):** Já implementado em `signals.py:194`. Se o mesmo `user-id` (ou `nick`) mandar 5 mensagens em 2 segundos, apenas a 1ª conta.
3. **Exigência de autores únicos (`unique_authors_10s >= 4`):** Evita flood de 1 usuário com múltiplas linhas.
4. **Filtro de comandos e sorteios/enquetes:**
   - Ignorar mensagens iniciadas por `!` (ex.: `!pix`, `!sorteio`, `!loja`, `!uptime`, `!comandos`), que geram picos artificiais quando o streamer anuncia um sorteio.
   - Ignorar ondas de número puro de 1 dígito (`"1"`, `"2"`) quando o streamer faz votação no chat (`signals.py:176` já filtra `key.isdigit() and len(key) <= 2` nas ondas, mas no gatilho online convém descontar mensagens que sejam apenas `"1"` ou `"2"` se representarem `>70%` da janela sem risadas/reações).
5. **Reconciliação com `CLEARCHAT`:** Se o IRC emitir `CLEARCHAT #canal :usuario` (ban/timeout de raid de spam bots), remover os incrementos daquele `usuario` nos últimos 10 s.

---

## 3. Gravação de Live: `streamlink` vs `yt-dlp` vs `ffmpeg`, Buffer Rolante e Orçamento no Piso 6 GB / 6 Threads

### 3.1. Comparativo: `streamlink` vs `yt-dlp` vs `ffmpeg` para Live da Twitch

Hoje, `clipes/ingest.py::record_live_stream` (linhas 132–175) usa `yt-dlp -g` para extrair a URL `.m3u8` e passa para `ffmpeg -i <m3u8> -c copy`. Na Twitch ao vivo, essa abordagem tem limitações importantes:

| Ferramenta | Vantagens na Twitch Live | Problemas / Limitações na Twitch Live |
|---|---|---|
| **`ffmpeg` direto na URL `.m3u8`** | Zero overhead extra; já instalado no container. | Token do manifesto HLS da Twitch (`usher.ttvnw.net`) tem validade curta; não filtra segmentos de anúncios (`EXT-X-DATERANGE` / `stitched-ad`); quando entra anúncio Server-Side (SSAI), o `ffmpeg` grava o comercial ou engasga na descontinuidade de codec/resolução. |
| **`yt-dlp` (`--downloader ffmpeg`)** | Já instalado no `Dockerfile`; ótimo para VODs e lives do YouTube. | Na Twitch ao vivo, apenas repassa a playlist HLS ao `ffmpeg`; não possui o motor de filtragem contínua de anúncios HLS nem prefetch de *low-latency* da Twitch. |
| **`streamlink` (`--stdout` -> `ffmpeg`)** | **Padrão ouro para Twitch Live:** filtra automaticamente segmentos de anúncios pre-roll e mid-roll (pausando o stream em vez de injetar o comercial no arquivo); suporta `--twitch-low-latency` (reduz atraso live edge para ~2 segmentos); reconecta transparente e entrega um fluxo MPEG-TS limpo via `stdout` para o `ffmpeg`. | Adiciona ~25–35 MB de RAM por instância Python (pacote `streamlink` via `pip`). |

**Fontes verificadas (2026-11-17):**
- [Streamlink Documentation — Twitch Plugin & CLI Options (`--twitch-low-latency`, ad filtering)](https://streamlink.github.io/cli/plugins/twitch.html)
- [FFmpeg Formats Documentation — `segment` / `stream_segment` muxer](https://ffmpeg.org/ffmpeg-formats.html#segment_002c-stream_005fsegment_002c-ssegment)

### 3.2. Buffer Rolante em Segmentos (`ffmpeg -f segment`) com Pre-Roll

**Por que o buffer rolante é obrigatório?**
1. **O pico no chat acontece DEPOIS do início da jogada/fala:** Como documentado em `clipes/signals.py:25` (`CHAT_SHIFT_S = 5.0`) e `clipes/snap.py:10-12` (`IDEAL_PRE_ROLL = 6–12s`, ou até 45–90s em histórias longas da Decisão 3 e 5), se você só abrir a conexão de vídeo *depois* que o pico no chat disparar, você:
   - Perde todo o contexto anterior ao pico (o setup da piada ou início do assunto); e
   - Toma um **pre-roll ad de 15 a 30 segundos** da Twitch no momento em que abre a stream!
2. **Como implementar o Ring Buffer com `streamlink` + `ffmpeg -f segment`:**
   ```sh
   streamlink --twitch-low-latency \
     --hls-live-edge 2 \
     "https://www.twitch.tv/${CHANNEL}" \
     "720p60,720p,best" --stdout \
   | ffmpeg -hide_banner -loglevel warning \
     -fflags +genpts+igndts \
     -i pipe:0 \
     -map 0:v:0 -map 0:a:0 \
     -c copy \
     -f segment \
     -segment_time 6 \
     -segment_format mpegts \
     -reset_timestamps 1 \
     "/tmp/ring/${CHANNEL}/seg_%05d.ts"
   ```
   - **Por que `.ts` (`mpegts`) de 6 s e NÃO `.mp4` ou `-segment_wrap`?**
     - Arquivos `.ts` não têm átomo `moov` no final: podem ser concatenados instantaneamente via `ffmpeg -f concat -safe 0 -i list.txt -c copy window.mkv` sem re-encode e sem corromper caso um segmento seja lido logo após fechar.
     - Em vez de usar `-segment_wrap` (que sobrescreve índices e exige cuidado com ordenação circular quando um clipe está sendo extraído), é mais simples e seguro deixar o índice `%05d` crescer monotonicamente e ter uma rotina leve no monitor Python (a cada 10 s) que apaga segmentos com `mtime > 180s` (exceto se o canal estiver em estado `CAPTURING`, ou fazendo `os.link()` — *hardlink* instantâneo de custo zero de disco — dos segmentos do pre-roll para a pasta da tarefa na fila!).
   - **Como capturar um assunto completo (até ~3 min, Decisões 3 e 5):**
     - O ring buffer mantém constantemente os últimos **90 segundos** (15 segmentos de 6 s) em disco/tmpfs.
     - Quando o chat (ou RMS) dispara um pico em $t_{\text{pico}}$:
       1. O monitor cria hardlinks dos últimos **60 a 90 s** de segmentos `.ts` anteriores ao pico (garantindo o início da história).
       2. Continua coletando os próximos segmentos por **+60 a +120 s** após o pico (ou até a fala/chat voltar ao baseline por >15 s), gerando um arquivo bruto `source.mkv` de **~2,5 a 3,0 minutos** via `ffmpeg -f concat -c copy` (< 0,5 s de CPU).

### 3.3. Consumo de CPU, RAM, Disco e Banda no Piso (6 GB RAM / 6 Threads)

Vamos calcular o custo real por canal gravado em modo `-c copy` (sem transcodificação durante a captura):

| Recurso | 1 Canal em `1080p60` (`source`) | 1 Canal em `720p60` / `720p30` (**Recomendado**) |
|---|---|---|
| **Bitrate de rede (Banda)** | ~6,0 a 8,0 Mbps (~45–60 MB/min) | **~2,5 a 4,5 Mbps (~19–34 MB/min)** |
| **CPU (`streamlink` + `ffmpeg -c copy`)** | ~1,5% a 3% de 1 thread | **~1% a 2% de 1 thread** |
| **RAM (`streamlink` + `ffmpeg`)** | ~45–60 MB RSS total por canal | **~40–55 MB RSS total por canal** |
| **Disco/tmpfs (Ring buffer de 3 min)** | ~135–180 MB por canal | **~60–100 MB por canal** |

> **Observação Crítica de Engenharia (Resolução `720p` vs `1080p` no layout vertical):**
> Olhando `clipes/render.py` (linhas 254–258), o vídeo final é `1080x1920` (9:16), onde o quadro principal `16:9` (`[fg]scale=1080:1920:force_original_aspect_ratio=decrease`) ocupa exatos **`1080 x 608` pixels** no centro da tela, e o fundo é reduzido para `270x480` com `boxblur=10:2`!
> Uma fonte `720p` (`1280 x 720`) já possui resolução nativa **maior** que `1080 x 608` na vertical (`720 > 608`) e na horizontal (`1280 > 1080`)! Gravar o buffer rolante em `720p60,720p` em vez de `1080p60`:
> 1. **Corta pela metade o consumo de banda e I/O de disco** (~3 Mbps vs ~7 Mbps);
> 2. **Reduz em ~40% o tempo de CPU do decode H.264** no `ffmpeg` durante o `render_clip`;
> 3. Tem **zero perda visual perceptível** no quadro `1080x608` embutido no vertical `1080x1920`.

#### Quantas gravações simultâneas são realistas no piso de 6 GB RAM / 6 threads?
Lembrando que o container tem **6 GB de RAM dura** (`mem_limit: 6g` em `docker-compose.yml:6`) e precisa reservar:
- ~1,5 a 2,2 GB de RAM e 4–6 threads para o worker sequencial (`faster-whisper small int8` OU `LLM local Q4_K_M` OU `ffmpeg render libx264`);
- ~500 MB de margem para o kernel/buffers Python/NumPy.

Portanto, o orçamento seguro e realista para o monitor contínuo é:
- **Monitoramento de Chat (IRC `justinfan`):** **50 a 80 canais simultâneos** da categoria (~20 MB RAM total, `< 1%` CPU, `< 30 KB/s` de rede).
- **Gravação Contínua em Ring Buffer (`720p`, `-c copy`):** **3 a 5 canais simultâneos** (os top 3–5 canais com maior atividade/velocidade de chat no momento):
  - **RAM total dos 4 gravadores:** `4 × 50 MB ≈ 200 MB` (apenas 3,3% dos 6 GB!).
  - **CPU total dos 4 gravadores:** `~5% a 8%` de 1 única thread (deixando >5,9 threads livres para o Whisper/FFmpeg/LLM).
  - **Banda de rede total (4 canais em 720p):** `~12 a 18 Mbps` (cabe folgado em qualquer conexão residencial de 100+ Mbps).
  - **Espaço do Ring Buffer (4 canais × 3 min):** `~320 a 400 MB` (cabe inteiramente em tmpfs `/dev/shm` ou `/tmp`, poupando desgaste de SSD!).
- *(Se a banda de rede permitir ~35 Mbps, o hardware aguenta até **8 gravações simultâneas** em `720p` com `-c copy` gastando ~400 MB de RAM, mas **4 gravações simultâneas rotativas** guiadas pelo termômetro do IRC cobrem os canais ativos com folga máxima de memória).*

### 3.4. Como Evitar Anúncios / Pre-Roll Injetados na Twitch

A Twitch usa *Server-Side Ad Insertion* (SSAI / SureStream), injetando segmentos de anúncio diretamente na playlist HLS `.m3u8` marcados com `#EXT-X-DATERANGE:CLASS="twitch-stitched-ad"`.
Existem 4 camadas práticas de mitigação (do mais simples ao mais completo):

1. **Evitar o Pre-Roll mantendo a conexão aberta (Ring Buffer):**
   - O *pre-roll* (anúncio de 15–30 s ao abrir a live) só ocorre nos primeiros segundos ao iniciar a sessão HLS. Como os canais monitorados já estão com o `streamlink` aberto em ring buffer contínuo, o pre-roll acontece apenas uma vez na abertura do canal e nunca no momento do pico!
2. **Filtragem nativa do Streamlink (ativa por padrão nas versões atuais):**
   - O plugin da Twitch do `streamlink` detecta as tags `stitched-ad` / `EXT-X-DATERANGE` e descarta os segmentos do comercial automaticamente. Assim, **nenhum frame de anúncio é gravado nos arquivos `.ts`** (durante um mid-roll sem stream limpa, o fluxo apenas pausa até o fim do anúncio).
3. **Detecção de lacuna de anúncio no Monitor:**
   - Se durante uma janela candidata ocorrer pausa por mid-roll (detectável pelo log do `streamlink`: `Filtering out segments and pausing stream output` ou queda abrupta na criação de segmentos `.ts`), o monitor descarta ou trunca a janela antes do anúncio para não gerar clipe incompleto.
4. **Bypass total de Mid-Roll (Opcional, se o usuário tiver conta Twitch Turbo ou proxy):**
   - Caso o usuário possua assinatura Twitch Turbo (ou token de conta sem anúncios), basta passar `--twitch-api-header "Authorization=OAuth <token>"` ao `streamlink` ([Streamlink Twitch Docs](https://streamlink.github.io/cli/plugins/twitch.html#authentication), verificado em 2026-11-17). Sem isso, a combinação de Ring Buffer + filtro nativo do Streamlink já elimina 100% dos anúncios do vídeo final e 100% dos pre-rolls em picos.

---

## 4. Create Clip API (`POST /helix/clips`): Limites, Atualizações Recentes e Uso como Complemento

### 4.1. Especificação Oficial e Limites (`dev.twitch.tv`)

Existem três endpoints relacionados a Clips na API Helix:

1. **`POST https://api.twitch.tv/helix/clips` (Create Clip — ao vivo):**
   - **Autenticação:** Exige obrigatoriamente um **User Access Token** (NÃO aceita App Access Token) com o escopo **`clips:edit`**.
   - **Parâmetros (`query`):**
     - `broadcaster_id` *(string, obrigatório)*: ID do canal ao vivo.
     - `has_delay` *(boolean, opcional, padrão `false`)*: adiciona pequeno atraso para compensar latência de visualização.
     - `title` *(string, opcional)* e `duration` *(float, opcional, de **`5` a `60` segundos** com precisão de `0.1`, padrão `30`)* — **adicionados na atualização de 19/12/2025 do Changelog da Twitch API**.
   - **Processamento assíncrono:** Retorna `202 Accepted` com `id` (slug) e `edit_url`. É necessário consultar `GET /helix/clips?id=<id>` por até 15 s para confirmar que o clipe foi efetivamente processado.
   - **Limitações severas para automação em canais de terceiros:**
     1. **Teto duro de 60 segundos** e recorte ancorado no instante da chamada (não permite recortar livremente uma história de 2–3 minutos nem escolher `start_time` e `end_time` exatos baseados nas frases do Whisper).
     2. **Bloqueio de permissão por canal:** A maioria dos streamers médios/grandes configura os clipes do canal como **Somente Seguidores (Followers-only, ex.: seguir há >10 min ou >24h)** ou **Somente Inscritos (Subscribers-only)**. Se a conta do bot não seguir o canal pelo tempo exigido ou não for sub, `POST /helix/clips` falha!
   - **Fontes:** [Twitch API Reference — Create Clip](https://dev.twitch.tv/docs/api/reference/#create-clip) e [Twitch API Changelog — 2025-12-19](https://dev.twitch.tv/docs/change-log/) (verificado em 2026-11-17).

2. **`POST https://api.twitch.tv/helix/videos/clips` (Create Clip From VOD):**
   - Permite criar clipe informando `video_id`, `vod_offset` e `duration` (5 a 60 s), **porém exige os escopos `channel:manage:clips` ou `editor:manage:clips`** — ou seja, **só funciona no próprio canal ou em canais onde a conta é Editora oficial**, sendo inutilizável para monitorar canais de terceiros de uma categoria.
   - **Fonte:** [Twitch API Reference — Create Clip From VOD](https://dev.twitch.tv/docs/api/reference/#create-clip-from-vod) (verificado em 2026-11-17).

3. **`GET https://api.twitch.tv/helix/clips` (Get Clips):**
   - **Autenticação:** Aceita **App Access Token** gratuito (sem escopo de usuário!).
   - Permite consultar `GET /helix/clips?broadcaster_id=<id>&started_at=<iso8601>` para listar clipes que os **próprios espectadores da live** acabaram de criar nos últimos minutos!
   - **Fonte:** [Twitch API Reference — Get Clips](https://dev.twitch.tv/docs/api/reference/#get-clips) (verificado em 2026-11-17).

### 4.2. Por que NÃO usar `Create Clip` como fonte de vídeo, mas QUANDO usar como complemento

- **Por que não serve como fonte de vídeo:** Conflita diretamente com as **Decisões 3 e 5** do projeto (assuntos de até ~3 minutos divididos em Partes 1/N de 45–60 s com corte determinístico em borda de frase pelo `snap.py`). O clipe da Twitch tem no máximo 60 s e corta o assunto de forma cega.
- **Dois usos excelentes como complemento:**
  1. **Sinal gratuito de confirmação via `GET /helix/clips` (sem User Token):** Quando nosso monitor detectar um pico no chat ou após 60 s do pico, consultar `GET /helix/clips?broadcaster_id=<id>&started_at=<t_pico - 60s>` com o App Access Token. Se espectadores reais criaram 1 ou mais clipes naquele mesmo minuto (`view_count`, `title` dado pelo viewer, `url`), ganhamos:
     - Um **bônus fortíssimo de confirmação do pico** (sinal `clips_activity` análogo ao `heatmap` do YouTube!);
     - Uma **URL oficial de clipe (`https://clips.twitch.tv/<slug>`)** já pronta para usar nos créditos (Decisão 4) sem precisar de User Token nem `clips:edit`!
  2. **Link oficial permanente via `POST /helix/clips` (opcional, se houver User Token):** Se o bot tiver um User Token opcional com `clips:edit` e nenhum viewer tiver clipado, disparar `POST /helix/clips` em background (best-effort) apenas para gerar um link curto oficial `clips.twitch.tv/<slug>` para os metadados de crédito (útil porque muitos streamers apagam os VODs da Twitch ou deixam VODs apenas para inscritos, enquanto o link de Clip permanece público). Se falhar por restrição de seguidor/sub, o crédito usa `https://twitch.tv/<user_login>`.

---

## 5. Direitos Autorais, Termos de Serviço (ToS) e Riscos Práticos

> *Aviso: Esta seção sintetiza exclusivamente o que consta nos documentos oficiais públicos da Twitch e diretrizes de plataforma verificados em 2026-11-17. Não constitui aconselhamento jurídico.*

### 5.1. O que dizem os Termos de Serviço e o Acordo de Desenvolvedor da Twitch

1. **Titularidade do Conteúdo de Usuário (*User Content* — Seção 8 do Twitch ToS):**
   - O streamer (criador) mantém os direitos autorais sobre sua transmissão ao vivo, imagem, voz e VODs/Clipes.
   - Ao transmitir na Twitch, o streamer concede licença ampla **à Twitch** para operar e promover o serviço, e concede aos demais usuários da Twitch permissão limitada **apenas na medida necessária para usar as funcionalidades de compartilhamento nativas da própria plataforma** (como embeds oficiais ou links `clips.twitch.tv`).
   - **Republicação em outras plataformas (Instagram Reels, TikTok, YouTube Shorts):** Baixar/gravar o fluxo de vídeo bruto de terceiros e fazer re-upload nativo como arquivo `.mp4` em outra plataforma **não é coberto pela licença automática entre usuários da Twitch**. Sem autorização do streamer (expressa, via programa de cortes/afiliados, ou tolerância tácita do criador), o streamer é o detentor dos direitos e pode solicitar remoção por direitos autorais (DMCA takedown) na plataforma de destino.
   - **Fonte:** [Twitch Terms of Service — Section 8 (User Content) & Section 9 (Prohibited Conduct)](https://legal.twitch.com/legal/terms-of-service/) (verificado em 2026-11-17).

2. **Twitch Developer Services Agreement:**
   - Proíbe o uso de *Program Materials* (APIs da Twitch) para replicar a experiência principal da Twitch ou redistribuir conteúdo de usuários em violação aos Termos de Serviço e à privacidade dos criadores.
   - **Fonte:** [Twitch Developer Services Agreement](https://legal.twitch.com/legal/developer-agreement/) (acesso em 2026-11-17).

### 5.2. DMCA e Música em Lives

- **A armadilha da música ao vivo vs VOD:** Na Twitch, o sistema da Audible Magic frequentemente silencia trechos com música comercial protegida nos **VODs** gravados, mas **o áudio da live ao vivo contém a música integral**.
- Quando o monitor grava a live ao vivo (`streamlink`), qualquer música tocando ao fundo (Spotify do streamer, alertas musicais, rádio do jogo em GTA RP) estará presente na faixa de áudio do clipe.
- Ao publicar no Instagram, TikTok ou YouTube Shorts, os sistemas automatizados de impressão digital de áudio (*Meta Rights Manager*, *YouTube Content ID*, *TikTok Copyright Check*) detectam fonogramas comerciais em poucos segundos, podendo resultar em:
  - Silenciamento automático (mute) do vídeo publicado;
  - Bloqueio em certos países ou desmonetização;
  - *Copyright strike* (3 strikes = encerramento da conta nas plataformas, conforme [Twitch DMCA Guidelines](https://legal.twitch.com/legal/dmca-guidelines/) e regras equivalentes da Meta/YouTube).
- **Fonte:** [Twitch DMCA Guidelines](https://legal.twitch.com/legal/dmca-guidelines/) e [Twitch Music Guidelines](https://www.twitch.tv/p/en/legal/community-guidelines/music/) (verificado em 2026-11-17).

### 5.3. Crédito vs Permissão e Mitigações Práticas para o Bot

- **Crédito é indispensável na cultura de lives, mas não substitui permissão legal:**
  - Na comunidade brasileira de lives (ex.: *Just Chatting*, *GTA RP*, podcasts), muitos streamers incentivam ou toleram páginas de cortes desde que haja **crédito claro e visível** (nome do canal no vídeo + menção/link na legenda, exatamente como definido na **Decisão 4**). Porém, outros streamers mantêm canais próprios de cortes exclusivos e dão strike em páginas não autorizadas.
- **Mitigações técnicas e operacionais recomendadas para o `clipes-lives`:**
  1. **Whitelist / Blacklist (`opt-out`) de canais no monitor:** Manter no arquivo de configuração uma lista `blocked_channels` (streamers que proíbem cortes de terceiros ou canais de TV/retransmissão esportiva) e `allowed_channels` opcional.
  2. **Crédito queimado no vídeo (`render.py`) + metadados (`clips.json`):** Além do link na descrição, incluir uma tag discreta no `.ass` ou overlay (`🎥 twitch.tv/<canal>`) no topo do quadro `16:9` (cumpre a Decisão 4 mesmo quando o vídeo é repostado).
  3. **Filtro de música / fala:** Como o pipeline já roda Silero VAD + `faster-whisper` (`clipes/asr.py:98-107`), descartar automaticamente janelas onde a densidade de palavras faladas for muito baixa (< 1,0 palavra/s) ou onde não houver fala clara (evita clipar trechos puramente musicais).
  4. **Fila local com revisão antes de postar (Decisão 2):** Nesta rodada o sistema apenas **detecta, grava e enfileira** (sem publicar automaticamente), o que elimina o risco imediato e permite descartar clipes com música protegida ou contexto sensível.

---

## 6. Desenho Sugerido do "Monitor" para o Piso de Hardware (6 GB RAM / 6 Threads)

Seguindo os princípios do projeto (simples, determinístico, sem dependências pesadas, respeitando o limite duro de **6 GB RAM / 6 threads** e **execução estritamente sequencial de modelos pesados**), propomos uma arquitetura de **2 processos cooperativos comunicando-se via sistema de arquivos (SQLite ou JSON atômico em disco)**:

```
┌────────────────────────────────────────────────────────────────────────────┐
│ CONTAINER DOCKER (mem_limit: 6g, cpus: 6, sem GPU)                         │
│                                                                            │
│  [Processo 1: monitor_daemon (~220 MB RAM total, ~0.3 thread)]             │
│   ├─ Helix Poller (a cada 60s): GET /helix/streams?game_id=...&language=pt │
│   ├─ IRC Reader (asyncio, justinfan): 50-80 canais (rate limit 1 JOIN/0.6s)│
│   │   └─ Calcula score online 1 Hz (janela 10s vs mediana 180s + Z-score)  │
│   ├─ Pool de 4 Ring Buffers (streamlink 720p -> ffmpeg -c copy seg 6s .ts) │
│   │   └─ Alocado nos top 4 canais ativos; mantém últimos 90s em /tmp/ring  │
│   └─ No disparo de pico:                                                   │
│       ├─ Hardlink dos últimos 90s (.ts) + grava mais +90s a +120s pós-pico │
│       ├─ Concatena (-c copy) -> /out/queue/pending/<job_id>/source.mkv     │
│       └─ Salva chat.json + meta.json (canal, link, timestamp, score)       │
│                                    │                                       │
│                                    ▼ (Fila em disco: /out/queue/pending/)  │
│                                                                            │
│  [Processo 2: pipeline_worker (SEQUENCIAL, 1 job por vez, 5 threads)]      │
│   ├─ Etapa A: Extrai audio.wav (16kHz mono) + calcula signals.py (RMS+chat)│
│   ├─ Etapa B: faster-whisper small int8 (carrega -> transcreve -> UNLOAD!) │
│   ├─ Etapa C: Segmentação de Assunto (até ~3 min -> Parte 1/N de 45-60s)   │
│   │   └─ Cortes em borda de frase (.?!) + gancho + fade/encerramento       │
│   ├─ Etapa D: LLM Local Leve Q4 (carrega -> título/legenda/nota -> UNLOAD!)│
│   └─ Etapa E: ffmpeg render 1080x1920 + crédito do canal + move p/ ready/  │
└────────────────────────────────────────────────────────────────────────────┘
```

### 6.1. Detalhamento de Processos, Filas e Limites de Concorrência

1. **Processo 1 — `clipes.monitor` (Leve, Sempre Ativo):**
   - **Descoberta (`Helix Poller`):** A cada 60 s, consulta `GET /helix/streams?game_id=<id>&language=pt&first=100` (ou usa lista de canais configurada se rodar sem `TWITCH_CLIENT_ID`). Filtra canais na `blacklist`.
   - **Termômetro de Chat (`IRC Reader`):** Uma única coroutine `asyncio` conectada a `wss://irc-ws.chat.twitch.tv:443` como `justinfan<rand>`, entrando nos canais com espaçamento de `0.6 s` entre `JOIN`s (máx. 80 canais). Mantém em memória apenas um `deque(maxlen=180)` de contagens por segundo por canal (< 5 MB de RAM).
   - **Pool de Gravação (`Ring Buffer Manager`):**
     - Limite duro de concorrência: **`MAX_RECORDERS = 4`** processos `streamlink ... 720p60,720p,best --stdout | ffmpeg -c copy -f segment -segment_time 6`.
     - Quais 4 canais gravar? Os 3 canais com maior taxa de mensagens/reações nos últimos 5 minutos + 1 slot dinâmico para canais que estão começando a esquentar (histerese mínima de 5 minutos para não ficar trocando de canal a cada segundo).
     - Limpeza contínua: a cada 10 s, remove segmentos `.ts` com mais de **90 s** (mantendo 15 segmentos × 6 s = 90 s de pre-roll constante por canal, ocupando ~240 MB totais em `/tmp/ring`).
   - **Captura de Pico (Window Freeze):**
     - Quando o canal $C$ dispara um pico em $t_{\text{pico}}$:
       - Se $C$ já está em um dos 4 ring buffers: congela (via hardlink `os.link`) os 15 segmentos anteriores (**90 s de pre-roll**) e aguarda mais **90 a 120 s de post-roll** (ou até o RMS/chat silenciar por >15 s), gerando um arquivo bruto `source.mkv` de **~2,5 a 3,0 minutos** via `ffmpeg -f concat -c copy` (< 0,5 s de CPU).
       - Cooldown por canal após captura: `CHANNEL_CLIP_COOLDOWN = 180s` para não duplicar o mesmo assunto.

2. **Estado em Disco e Filas (Zero Banco de Dados — Diretórios Atômicos POSIX):**
   - Sem Redis, sem Postgres, sem Mongo (respeitando a regra global de nunca usar banco e mantendo simplicidade máxima):
   - Estrutura em `${OUT_DIR}/queue/`:
     - `pending/<timestamp>_<channel>_<stream_id>/`: `source.mkv` (~60–90 MB), `chat.json` (mensagens capturadas pelo IRC durante os ~3 min da janela, já no formato `[t_rel, text, author_idx]` esperado por `clipes/signals.py`!), `meta.json` (`broadcaster_login`, `broadcaster_name`, `stream_url`, `game_name`, `peak_offset_s`, `chat_zscore`).
     - `processing/<job_id>/`: movido via `Path.replace()` atômico quando o worker assume o job.
     - `ready/<job_id>/`: contém `clip_01_part1of2.mp4`, `clip_01_part2of2.mp4`, `clips.json` (com `score_ia`, `title`, `caption`, `hashtags`, `credit_url`, `part_index`, `part_total`).
     - `rejected/<job_id>/`: jobs descartados por nota da IA abaixo do corte ou falta de fala, mantendo apenas `clips.json` (apagando `source.mkv` para não encher o disco).
   - **Limite da fila `pending/`:** Máximo de **10 jobs pendentes** em disco (~800 MB máx.). Se a fila encher porque houve muitos picos simultâneos, um novo pico só entra se tiver `chat_zscore` maior que o pior job de `pending/` (substituindo-o).

3. **Processo 2 — `clipes.worker` (Pesado, Estritamente Sequencial, 1 Job por Vez):**
   - Usa **`CLIPES_CPU_THREADS = 5`** (deixando 1 thread folgada para o `monitor_daemon` nunca perder pacotes IRC/HLS).
   - **Orçamento de Memória Sequencial (nunca dois modelos juntos na RAM):**
     1. **Sinais (`signals.py`):** Extrai `audio.wav` (3 min = ~5,7 MB) e funde `signal_loudness` + `signal_chat` (com o `chat.json` gravado ao vivo pelo IRC!). Pico de RAM: ~80 MB.
     2. **Transcrição (`asr.py`):** Roda `faster-whisper small` (`int8`, 5 threads) nos ~3 min do `source.mkv` (leva ~50–65 s de CPU, ~1,3 GB de RAM). Salva `words.json` e **descarrega explicitamente o modelo (`del model; gc.collect()`)** — ou roda em subprocesso isolado para devolução garantida de 100% da RAM ao SO!
     3. **Corte Inteligente de Assunto e Partes 1/N (`snap.py` evoluído para Decisões 3, 4 e 5):**
        - Em vez de forçar uma janela única de no máximo 55 s centrada no pico, encontra o **início real do assunto** antes do pico (pausa longa `>= 1.0s` ou início de bloco temático até 90 s antes do pico) e o **fechamento real do assunto** após o pico (fim de frase + queda de energia/reação até 120 s após o pico).
        - Se o assunto completo durar $\le 60\text{ s}$: gera 1 clipe único.
        - Se durar entre $60\text{ s}$ e $180\text{ s}$: divide deterministicamente em **Partes de 45–60 s (`Parte 1/N`)**, onde cada corte intermediário ocorre em borda de frase (`.?!`) próxima a uma frase de alta tensão/pergunta (gancho), com sobreposição de ~1,5 s na parte seguinte.
     4. **Avaliação e Metadados via LLM Local (`llm.py` em subprocesso isolado):**
        - Só carrega o GGUF local (ex.: Qwen2.5-1.5B/3B-Instruct Q4_K_M, ~1,2–2,0 GB RAM) *depois* que o Whisper terminou. Gera nota `0-10`, título, legenda, hashtags e valida se o corte tem começo/meio/fim coerentes. Descarrega o processo ao terminar.
     5. **Renderização Vertical com Crédito e Encerramento (`render.py`):**
        - Renderiza `1080x1920` adicionando:
          - **Crédito visível (Decisão 4):** Cabeçalho fixo no `.ass` (`Style: Credit`) com `🎥 twitch.tv/<canal>` + indicação `PARTE 1/2` (quando aplicável) e `credit_url` no `clips.json`.
          - **Encerramento limpo (Decisão 4):** Padding final de `+0.4s` pós-fala/reação + filtro `afade=t=out:st=<dur-0.35>:d=0.35` e `fade=t=out:st=<dur-0.35>:d=0.35` no `ffmpeg`, eliminando qualquer sensação de corte seco no último frame.

---

## 7. Resumo das Fontes Verificadas

1. **Twitch API — Get Streams:** `https://dev.twitch.tv/docs/api/reference/#get-streams` (acesso em 2026-11-17)
2. **Twitch API — Get Games:** `https://dev.twitch.tv/docs/api/reference/#get-games` (acesso em 2026-11-17)
3. **Twitch API — OAuth Client Credentials Grant Flow:** `https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/#client-credentials-grant-flow` (acesso em 2026-11-17)
4. **Twitch API — Rate Limits (800 pts/min):** `https://dev.twitch.tv/docs/api/guide/#rate-limits` (acesso em 2026-11-17)
5. **Twitch Chat & IRC — Rate Limits & Authentication:** `https://dev.twitch.tv/docs/irc/#rate-limits` e `https://dev.twitch.tv/docs/irc/authenticate-bot/` (acesso em 2026-11-17)
6. **Twitch EventSub — `channel.chat.message` & WebSocket Limits:** `https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types/#channelchatmessage` e `https://dev.twitch.tv/docs/eventsub/handling-websocket-events/#limits` (acesso em 2026-11-17)
7. **Twitch API — Create Clip, Create Clip From VOD & Get Clips:** `https://dev.twitch.tv/docs/api/reference/#create-clip`, `https://dev.twitch.tv/docs/api/reference/#create-clip-from-vod` e `https://dev.twitch.tv/docs/api/reference/#get-clips` (acesso em 2026-11-17)
8. **Twitch API — Change Log (2025-12-19 update: `title` e `duration` 5–60s no Create Clip):** `https://dev.twitch.tv/docs/change-log/` (acesso em 2026-11-17)
9. **Streamlink — Twitch Plugin CLI Documentation:** `https://streamlink.github.io/cli/plugins/twitch.html` (acesso em 2026-11-17)
10. **FFmpeg — Segment Muxer (`-f segment`):** `https://ffmpeg.org/ffmpeg-formats.html#segment_002c-stream_005fsegment_002c-ssegment` (acesso em 2026-11-17)
11. **Twitch Legal — Terms of Service (Section 8 User Content):** `https://legal.twitch.com/legal/terms-of-service/` (acesso em 2026-11-17)
12. **Twitch Legal — Developer Services Agreement:** `https://legal.twitch.com/legal/developer-agreement/` (acesso em 2026-11-17)
13. **Twitch Legal — DMCA Guidelines & Music Guidelines:** `https://legal.twitch.com/legal/dmca-guidelines/` e `https://www.twitch.tv/p/en/legal/community-guidelines/music/` (acesso em 2026-11-17)
