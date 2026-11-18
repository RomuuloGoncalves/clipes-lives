# monitor-twitch Specification

## Problem Statement

O pipeline atual exige passar manualmente a URL de um VOD ou gravar uma única live por `--live-minutes` sem sinal de chat ao vivo. Para operar em alto volume na Twitch dentro do piso de hardware (Ryzen 5 5500U, 6 threads, container Docker sem banco de dados e sem chave de API obrigatória), precisamos vigiar dezenas de canais via chat IRC anônimo, detectar picos relativos à audiência de cada streamer, manter buffer rolante leve (`-c copy`) apenas nos canais quentes para garantir pré-roll de até 3 minutos (evitando o limite de 60s do Create Clip), e enfileirar jobs em diretórios JSON para um worker sequencial processar sem publicar.

## Goals

- [ ] Vigiar até `80` canais simultâneos via IRC anônimo (`justinfan`) em 1 processo `asyncio` (`< 100 MB` RAM) com descoberta padrão via `channels.txt` e suporte opcional a Helix Get Streams via variáveis de ambiente.
- [ ] Detectar picos relativos por canal em janelas de `10s` (`count10 >= 6`, `autores_unicos10 >= 4`, `z >= 3.5`, `count10 / expected >= 3.0`) ignorando bots, comandos `!` e duplicatas de Shared Chat (`source-room-id`).
- [ ] Gravar com `streamlink` + `ffmpeg -c copy` em buffer rolante de `5 min` (`300s`) para no máximo `3` canais quentes + `1` sob demanda, preservando pré-roll de até `180s` e pós-roll de `60–120s`, enfileirando em `OUT_DIR/queue/{pending,processing,done,rejected}/` e processando `1` job por vez sem publicar.
- [ ] Medir no piso Ryzen 5 5500U o consumo de 3 gravações simultâneas + worker (M5) e a estabilidade de 1 hora de IRC em 50–80 canais (M6).

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
| ------- | ------ |
| Publicação automática (TikTok, Reels, Shorts, Discord) | Excluído explicitamente pela decisão 2 do usuário nesta rodada |
| Twitch Create Clip API como fonte de vídeo | Exige user token `clips:edit` e limita a janela a 60s já cortados, conflitando com a decisão 5 (CONSOLIDADO §4) |
| Listagem de categoria via GraphQL interno da Twitch (`gql.twitch.tv`) | Quebra com rotação de hash e anti-bot e viola ToS (CONSOLIDADO §4) |
| Banco de dados ou broker externo (Redis, SQLite, Postgres, RabbitMQ) | Proibido pelas regras globais e pelo estilo ponytail; fila usa diretórios JSON + `os.replace` |

---

## Implicit-Requirement Dimensions Sweep

| Dimension | Resolution |
| --------- | ---------- |
| Input validation & bounds | Máximo de `80` canais no IRC, taxa de `JOIN` `<= 1` a cada `0.6s`, máximo de `3 + 1` processos `streamlink|ffmpeg`, buffer rolante limitado a `300s` por canal (`~150–200 MB` em disco por canal) |
| Failure / partial-failure states | Queda de socket IRC reconecta com backoff exponencial (`1s..60s`); queda de streamlink limpa segmentos órfãos; jobs deixados em `processing/` após crash voltam para `pending/` na inicialização |
| Idempotency / retry / duplicate handling | Transições de fila usam `os.replace` atômico; mensagens de Shared Chat com `source-room-id != room-id` são descartadas; cooldown de `180s` por canal impede disparos duplicados para o mesmo momento |
| Auth boundaries & rate limits | Padrão `channels.txt` sem credencial; Helix só lê `TWITCH_CLIENT_ID` e `TWITCH_CLIENT_SECRET` de `os.environ` (jamais gravados em disco) e respeita `Ratelimit-Remaining` / `Ratelimit-Reset` com polling `>= 60s` |
| Concurrency / ordering | `1` loop `asyncio` para IRC/descoberta, até `4` subprocessos leves de cópia de stream (`-c copy`), e estritamente `1` worker sequencial de ASR/LLM/render; jobs prontos são ordenados por `score` decrescente |
| Data lifecycle / expiry | Segmentos `.ts` do buffer rolante com idade `> 300s` são apagados a cada `10s`; canais sem atividade têm o buffer removido ao sair do top-3 quentes |
| Observability | Cada job grava `job.json` com timestamps, `z_score`, `count10`, `unique_authors`, `ready_at`, `status` e métricas de tempo do worker |
| External-dependency failure | Se Helix retornar HTTP 429/5xx ou credenciais faltarem, o monitor opera normalmente sobre `channels.txt`; se um canal exibir anúncio no `streamlink`, o filtro nativo pausa o stream e o ffmpeg usa `-reset_timestamps 1` |
| State-transition integrity | Máquina de estados estrita em disco: `pending -> processing -> done | rejected`; canal em `policy.json:blocked` nunca entra em gravação nem em `pending` |

---

## Assumptions & Open Questions

Every ambiguity is resolved or recorded here - nothing is left silently unclear.

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --------------------- | -------------- | --------- | ---------- |
| Descoberta padrão de canais | Arquivo local `channels.txt` (1 canal por linha); Helix Get Streams opcional via `TWITCH_CLIENT_ID`/`TWITCH_CLIENT_SECRET` no env | Concilia "monitorar categoria" com a restrição "sem chave de API" (CONSOLIDADO §4) | yes |
| Fórmula e limiares de pico relativo no chat | `expected = max(mediana_10s_180s_sem_ultimos_15s, 0.4*media, 2.5)`, `z = (count10 - expected)/sqrt(expected + 1)`, disparo em `count10 >= 6 and autores_unicos10 >= 4 and z >= 3.5 and count10/expected >= 3.0`, warmup `60s`, cooldown `180s` | Consolida os 3 relatórios B e reaproveita a modelagem Poisson de `signals.py:218` (CONSOLIDADO §4) | yes |
| Dimensionamento do buffer rolante | Até `3` canais quentes + `1` sob demanda, segmentos `.ts` de `10s` em `720p,480p,best` (`-c copy`), retenção de `300s` (pré-roll de `180s` + pós-roll de `60–120s`) | Cabe em `~600 MB` de disco total e poucos % de CPU, cobrindo histórias de até 3 min antes do pico (CONSOLIDADO §4) | yes |
| Política de direitos por canal (`policy.json`) | Suporte a `blocked`, `allowed` opcional e `delay_h` por canal (ex.: `gaules: 24`), preenchendo `ready_at` no manifesto do job | Atende regras públicas de streamers (ex.: Gaules exige 24h após fim da VOD, CONSOLIDADO §4 e §6.1) | yes |

**Open questions:** none - todas as decisões foram resolvidas no CONSOLIDADO ou registradas como premissas explícitas acima.

---

## User Stories

### P1: Descoberta de Canais, Política por Canal e Monitor IRC Relativo ⭐ MVP

**User Story**: Como operador do gerador de cortes, quero vigiar o chat de dezenas de canais da Twitch sem precisar de chave de API obrigatória e detectar picos de reação relativos ao tamanho de cada canal, respeitando bloqueios e janelas de atraso por streamer.

**Why P1**: Sem detecção relativa em tempo real e filtro de política, canais pequenos nunca disparam, canais grandes disparam a cada segundo, e canais bloqueados seriam gravados indevidamente.

**Acceptance Criteria**:

1. WHEN o monitor inicia sem `TWITCH_CLIENT_ID`/`TWITCH_CLIENT_SECRET` no ambiente THEN o sistema SHALL carregar a lista de canais de `channels.txt` e excluir qualquer canal listado em `policy.json` sob `blocked` (ou fora de `allowed`, quando `allowed` não for vazio).
2. WHERE `TWITCH_CLIENT_ID` e `TWITCH_CLIENT_SECRET` estiverem definidos em `os.environ` o sistema SHALL obter app access token OAuth2 em memória (sem jamais persistir token ou segredo em disco) e consultar `GET https://api.twitch.tv/helix/streams` com intervalo `>= 60s` respeitando `Ratelimit-Remaining`.
3. WHILE conectado ao IRC `irc.chat.twitch.tv` como `justinfan<N>` o sistema SHALL entrar em no máximo `80` canais espaçando comandos `JOIN` em `>= 0.6s`, descartando mensagens de `BOT_AUTHORS`, mensagens iniciadas por `!` e mensagens de Shared Chat cujo `source-room-id` difira de `room-id`.
4. WHEN um canal com tempo de monitoramento `>= 60s` (warmup) e fora de cooldown (`180s`) atingir em uma janela de `10s` simultaneamente `count10 >= 6`, `autores_unicos10 >= 4`, `z >= 3.5` e `count10 / expected >= 3.0` (onde `expected = max(mediana_10s_180s, 0.4 * media_10s, 2.5)`) THEN o sistema SHALL emitir um evento de pico contendo `channel`, `peak_ts`, `count10`, `unique_authors`, `expected` e `z_score`.

**Independent Test**: Rodar `docker compose run --rm clipes pytest tests/test_monitor.py` alimentando `ChannelSpikeDetector` com streams sintéticos de canais pequenos (`baseline=2.5`) e grandes (`baseline=40.0`), flood de 1 único autor, bots e tags `source-room-id`.

---

### P1: Gravação em Buffer Rolante (Streamlink + FFmpeg Copy), Fila em Disco e Worker Sequencial ⭐ MVP

**User Story**: Como operador no piso Ryzen 5 5500U, quero que apenas os canais quentes mantenham gravação em buffer rolante de 5 minutos sem recodificar vídeo e que cada pico gere um job autocontido (`source.mkv` + `chat.json` + `meta.json`) processado um por vez pelo pipeline existente.

**Why P1**: Garante o pré-roll de até 180s para não cortar o início do assunto (decisão 5) mantendo o uso de CPU/RAM dentro do limite do container.

**Acceptance Criteria**:

1. WHILE o monitor estiver ativo o sistema SHALL manter no máximo `3` subprocessos `streamlink --stdout <canal> 720p60,720p,480p,best | ffmpeg -c copy -f segment -segment_time 10` para os canais com maior atividade de chat mais `1` slot sob demanda, removendo segmentos `.ts` com idade `> 300s`.
2. WHEN um evento de pico ocorre em `T_pico` THEN o sistema SHALL preservar os segmentos de pré-roll `[T_pico - 180s, T_pico]` disponíveis no buffer, continuar gravando o pós-roll até a taxa de chat retornar a `<= 1.2 * expected` (limitado entre no mínimo `60s` e no máximo `120s`), concatenar com `ffmpeg -c copy` em `source.mkv`, salvar `chat.json` no formato `[t_rel, texto, autor_idx]` e mover atomicamente (`os.replace`) o diretório do job para `OUT_DIR/queue/pending/<job_id>/`.
3. WHEN `ingest_local` (via `cli.py --local-dir <job_dir>`) é chamado pelo worker THEN o sistema SHALL ingerir `source.mkv`, `meta.json` e `chat.json` diretamente sem invocar `yt-dlp`, extrair `audio.wav` e executar o pipeline (`signals -> candidates -> asr -> topics/snap -> llm -> render`) de forma estritamente sequencial (`1` job por vez).
4. WHEN o worker conclui um job THEN o sistema SHALL gravar `ready_at = created_at + delay_h * 3600` (conforme `policy.json`), mover o job atomicamente para `OUT_DIR/queue/done/<job_id>/` (ou `rejected/` se gerar `0` clipes válidos) e ordenar o índice da fila por `score` decrescente sem realizar publicação externa.

**Independent Test**: Executar `pytest tests/test_monitor.py` testando a máquina de estados `pending -> processing -> done/rejected`, recuperação de job órfão em `processing/` e execução completa do worker sobre um diretório local com `source.mkv` + `chat.json` sintéticos.

---

### P2: Medições no Piso Ryzen 5 5500U (M5 Gravação Simultânea e M6 IRC 1h)

**User Story**: Como engenheiro validando os limites do piso 5500U, quero medir em container o impacto real de 3 gravações simultâneas + worker (M5) e a estabilidade de 1 hora de conexão IRC em 50–80 canais (M6).

**Why P2**: Fecha as medições obrigatórias M5 e M6 do CONSOLIDADO §7 com critérios numéricos observáveis.

**Acceptance Criteria**:

1. WHEN o benchmark M5 (`tests/test_bench_m5_recorder.py`) executa 3 fluxos de segmentação `-c copy` simultâneos junto com 1 worker de processamento no container THEN o sistema SHALL verificar que o disco de buffer por canal permanece `<= 200 MB`, a CPU de cada gravador `-c copy` fica `< 25%` de 1 core e o RSS total do container permanece `<= 4.5 GB`.
2. WHEN o benchmark M6 (`tests/test_bench_m6_irc.py`) executa o cliente IRC/detector sobre 50–80 canais (live ou replay de 1h) THEN o sistema SHALL verificar RSS do monitor IRC `< 100 MB`, `0` desconexões fatais sem reconexão e `0` falsos picos em canais com `< 6` mensagens ou `< 4` autores únicos em `10s`.

**Independent Test**: Rodar `docker compose run --rm clipes pytest tests/test_bench_m5_recorder.py tests/test_bench_m6_irc.py -v -s`.

---

## Edge Cases

- IF um canal entrar em pico quando estava fora dos 3 canais quentes (usando o 4º slot sob demanda sem buffer prévio) THEN o sistema SHALL iniciar a gravação imediatamente com `pre_roll_actual = 0s`, gravar até `120s` de pós-roll e registrar `"partial_preroll": true` em `job.json`.
- IF o processo do monitor reiniciar enquanto existirem diretórios em `OUT_DIR/queue/processing/` THEN o sistema SHALL recolocar esses jobs em `OUT_DIR/queue/pending/` antes de iniciar o loop do worker.
- IF o `streamlink` encerrar com código não-zero porque a live terminou durante o pós-roll THEN o sistema SHALL concatenar os segmentos já capturados se a duração total for `>= min_dur` (`25.0s`) ou mover o job para `rejected/` caso contrário.

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| -------------- | ----- | ----- | ------ |
| MON-01 | P1: Descoberta via `channels.txt` e Helix opcional por env | Design | Pending |
| MON-02 | P1: Política por canal (`policy.json`: `blocked`, `allowed`, `delay_h`) | Design | Pending |
| MON-03 | P1: Cliente IRC anônimo `justinfan` (`<= 80` canais, JOIN `0.6s`, filtros) | Design | Pending |
| MON-04 | P1: Detector de pico relativo Poisson 10s (warmup 60s, cooldown 180s) | Design | Pending |
| MON-05 | P1: Gravador buffer rolante 5 min (`streamlink \| ffmpeg -c copy`, 3+1 canais) | Design | Pending |
| MON-06 | P1: Fila atômica em disco, `ingest_local` e worker sequencial sem publicar | Design | Pending |
| MON-07 | P2: Medições M5 (3 gravações + worker) e M6 (IRC 50–80 canais 1h) | Design | Pending |

**Coverage:** 7 total, 7 mapped to tasks, 0 unmapped

---

## Success Criteria

- [ ] Detector de pico relativo aprova momentos com `z >= 3.5`, `count10 >= 6`, `autores >= 4`, `ratio >= 3.0` e rejeita 100% dos floods de autor único, bots e duplicatas de Shared Chat.
- [ ] Buffer rolante preserva até `180s` de pré-roll e `60–120s` de pós-roll com `< 200 MB` de disco por canal.
- [ ] Worker processa jobs locais de ponta a ponta sem chamar `yt-dlp`, respeita `delay_h` em `ready_at` e nunca coexiste com segundo worker.
