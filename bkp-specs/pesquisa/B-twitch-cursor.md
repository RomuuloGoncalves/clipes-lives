# Pesquisa B: Twitch (API, monitor de categoria, gravação, direitos)

Pesquisador: agente Cursor. Fontes lidas em 2026-11-17. Não é aconselhamento jurídico.

## 1. Listar lives de uma categoria

- `GET https://api.twitch.tv/helix/streams?game_id=<id>&first=100`, paginado por `after=<cursor>`. Ordena por viewers, aceita até 100 `game_id`/`user_id` por chamada e filtra `language=pt` ([Streams ref.](https://dev.twitch.tv/docs/api/reference/streams)). Para achar o `game_id`: `GET /helix/games?name=...` ou `/search/categories`.
- Auth: **app access token** (client credentials: `client_id` + `client_secret` de um app registrado no console, grátis) + header `Client-Id`.
- Rate limit: token bucket por client ID, com bucket separado para app token e por usuário. O exemplo da doc mostra `Ratelimit-Limit: 800` por minuto ([API guide](https://dev.twitch.tv/docs/api/guide/)). Moderadores no fórum dizem que a resposta tem cache e recomendam **não consultar a mesma lista mais de 1x/min** ([fórum](https://discuss.dev.twitch.com/t/extension-getting-429s-when-calling-get-stream/36260)).
- Lista dinâmica: pode haver duplicados ou buracos entre páginas (doc). Deduplicar por `user_id`.

## 2. Chat

- **Limite de join concorrente: 100 canais por conta** (desde 2024-05-15). Vale para IRC e EventSub, exceto quando você é moderador ou quando o broadcaster autoriza o app com `channel:bot` ([Chat overview](https://dev.twitch.tv/docs/chat/), [anúncio](https://discuss.dev.twitch.com/t/giving-broadcasters-control-concurrent-join-limits-for-irc-and-eventsub/54997)).
- **Taxa de join: 20 joins por 10 s** por usuário em contas normais (IRC, ou EventSub com user token).
- IRC anônimo (`NICK justinfan<n>`, sem PASS real) funciona na prática, mas **nunca foi documentado**. A Twitch não diz como limita esse acesso, e um moderador do fórum alerta que provavelmente cai no mesmo limite de 100 ([fórum /17](https://discuss.dev.twitch.com/t/giving-broadcasters-control-concurrent-join-limits-for-irc-and-eventsub/54997/17)). O EventSub não aceita leitura anônima ([fórum](https://discuss.dev.twitch.com/t/explanation-of-channel-chat-message-event-auth/64378)). A doc oficial prefere EventSub e trata o IRC como legado ([Chat overview](https://dev.twitch.tv/docs/chat/)).
- Recomendação: **IRC justinfan, com ≤ 100 canais e joins a ≤ 2/s**, numa única conexão. Se precisar de mais de 100, abrir outra conexão com risco (**não verificado** se é bloqueado). Para o piso de hardware, 100 canais é mais que suficiente.
- **Pico relativo.** Já existe em `signals.py::signal_chat`: baseline por mediana móvel e `z = (count10 - expected10)/sqrt(expected10+1)`. No modo ao vivo, recomendo:
  - baseline por canal = EMA ou mediana dos últimos 10–15 min de contagens em 10 s;
  - disparo se `count10 ≥ max(6, 3×baseline)` **e** `z ≥ 4`;
  - cooldown de 60–90 s por canal.
  - Anti-flood/bots: contar **usuários únicos** por janela, não mensagens. Ignorar bots conhecidos (nightbot, streamelements, moobot, o próprio canal) e tags `msg-id` de sub/raid. Opcional: peso extra para mensagens de reação ("KKKK", "LUL", "?????", emotes).
  - Atenção ao **Shared Chat**: a mesma mensagem é duplicada nos canais combinados. Deduplicar por `source-id` ([IRC doc](https://dev.twitch.tv/docs/chat/irc/)).

## 3. Gravação

- **streamlink** (8.6.x): filtra os anúncios pre-roll/mid-roll automaticamente. **Não dá para desligar**, e o anúncio vira pausa/descontinuidade na saída ([plugin Twitch](https://streamlink.github.io/latest/cli/plugins/twitch.html), [discussão #6630](https://github.com/streamlink/streamlink/discussions/6630)). Evitar 100% só com Turbo/auth (`--twitch-api-header`). `--twitch-low-latency` só ajuda se o streamer ativou low latency.
- **yt-dlp** em live da Twitch: funciona (HLS), mas **não filtra anúncios** (**não verificado** nesta sessão). ffmpeg direto no `.m3u8` tem o mesmo problema e ainda exige resolver o token.
- Escolha: **`streamlink --stdout best | ffmpeg -c copy -f segment`**. Exemplo:
  `streamlink --twitch-low-latency -O twitch.tv/X 720p60,720p,480p,best | ffmpeg -i - -c copy -f segment -segment_time 10 -reset_timestamps 1 -strftime 1 buf/%s.ts`. Um processo de limpeza apaga segmentos com mais de 10 min. No pico, concatenar (concat demuxer) os segmentos de [pico−90 s, pico+120 s] e seguir gravando até fechar o assunto.
- Custo: `-c copy` não decodifica, então a CPU fica por conta de rede/parse, uns poucos % de núcleo por stream (**não medido**). O que limita é banda e o **render**. Bitrate típico Twitch 720p60 ≈ 3–4,5 Mbit/s, 1080p60 ≈ 6–8 Mbit/s (**não verificado**, ordem de grandeza). A 4 Mbit/s: **1,8 GB/h**, e um buffer de 10 min ≈ 300 MB por canal.
- Realista no piso (6 threads/6 GB): gravar **sob demanda**, não 100 canais. Só o chat fica em todos; ao detectar pico, gravar aquele canal. Como não há buffer antes do pico nesse caso, existem 2 opções:
  - (a) buffer rolante em **N=3–5 canais "quentes"** (maior taxa de chat), a ~4 Mbit/s cada ≈ 20 Mbit/s e ~1,5 GB de disco;
  - (b) usar a **VOD** (`/helix/videos?user_id=…&type=archive`, VOD da live em andamento) para buscar o trecho anterior ao pico com `yt-dlp --download-sections`. A (b) depende de o canal publicar VOD (**não verificado** quanto ao atraso).
  - Recomendo a (a) com N=3, mais a (b) como fallback.

## 4. Create Clip API

- `POST /helix/clips?broadcaster_id=…[&duration=5..60][&title=]` exige **user token com `clips:edit`**. Captura ~90 s em volta do momento da chamada e publica até 60 s. É assíncrono: confirmar com Get Clips em até 60 s ([Clips ref.](https://dev.twitch.tv/docs/api/reference/clips)). O clipe fica no nome do dono do token ([fórum](https://discuss.dev.twitch.com/t/clips-edit-scope-for-create-clip-clarification/33522)). Pode falhar com 403 se o canal restringe clipes a seguidores/subs.
- `Get Clips Download` e `Create Clip From VOD` exigem `editor:manage:clips`/`channel:manage:clips`, ou seja, **só sendo editor do canal**. Para terceiros, não serve.
- Quando usar: como **complemento**, para gerar o link oficial `clips.twitch.tv/…` (crédito e prova de origem). O limite de 60 s e a janela de 90 s conflitam com a decisão 5. Não usar como fonte de vídeo.

## 5. Direitos e ToS (o que as fontes dizem)

- ToS da Twitch §7: a licença é para uso pessoal. Proíbe "**downloading** … except as expressly permitted", "data mining, robots", e a §9.xi proíbe acesso por "robot, spider, scraper … or other automated means" ([ToS](https://legal.twitch.com/en/legal/terms-of-service), lido em 2026-11-17). A API oficial é o meio permitido. **Gravar a stream com streamlink não é API**, e esse é o risco principal de conta e IP.
- §8.a: se o streamer ativa os recursos de compartilhamento fora da Twitch, outros usuários recebem licença **para compartilhar via esse recurso** (botão Share → TikTok/YouTube/Instagram). Nesse caso o TikTok marca automaticamente `#canal`, sem opção de remover ([Help clips](https://help.twitch.tv/articles/en_US/Knowledge/how-to-use-clips)). Baixar e repostar fora desse fluxo **não** está coberto.
- DMCA: titulares de música/jogo podem pedir remoção ([DMCA Guidelines](https://legal.twitch.com/en/legal/dmca-guidelines/)). No YouTube, **Short > 1 min com claim é bloqueado** (ver A).
- **Regras dos próprios streamers:** o Gaules diz que "passa a autorizar a publicação de conteúdos retirados da Live **somente 24 horas após a finalização da VOD**", e isso vale para clipes e cortes (descrição de `youtube.com/watch?v=7pfvbXVmrtE`, lida em 2026-11-17). Canais de cortes BR costumam escrever "canal NÃO oficial" e "mande e-mail que eu removo" (ver A). Logo, a **fila precisa de uma regra por canal** (allowlist, atraso mínimo, crédito obrigatório).

## 6. Desenho sugerido do monitor (piso 6 threads/6 GB)

```
[discover] a cada 2-5 min: Get Streams(game_id, language=pt) -> state/streams.json (top N=100)
[chat]     1 processo asyncio, 1 conexao IRC justinfan, JOIN <=100 canais (2/s)
           -> contadores 10s por canal (usuarios unicos) -> baseline EMA -> evento pico
[record]   ate 3 streamlink|ffmpeg -c copy segment (canais quentes, buffer 10 min, limpeza)
           + 1 slot "on demand" para canal com pico fora dos quentes
[queue]    state/queue/*.json (um arquivo por pico: canal, t_pico, segmentos, status)
[worker]   1 por vez: concat -> pipeline atual (asr -> snap -> render -> LLM nota) -> state/out/
[policy]   allowlist/atraso por canal (ex.: gaules=24h) antes de "pronto p/ publicar"
```

- Estado em disco (JSON + rename atômico), sem banco e sem Redis. Ao reiniciar, retoma `queue/*.json` com `status!=done`.
- Concorrência: chat 1 processo (~50 MB, **não medido**); gravação ≤ 3+1 (CPU baixa com copy); **processamento estritamente 1 por vez** (Whisper e depois LLM, nunca juntos).
- Teto de backlog: se `queue` passar de X horas de render, descartar picos com nota prévia (sinal de chat) mais baixa.
