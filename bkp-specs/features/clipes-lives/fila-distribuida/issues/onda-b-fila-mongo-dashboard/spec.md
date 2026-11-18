# Onda B: coordenação no MongoDB entre PCs, painel, capacidade dos PCs e live contínua

## Problem Statement

Hoje cada PC tem sua fila em disco (`DiskQueue`) e um `flock` local (`clipes/monitor.py:650-800`): dois PCs vigiando o mesmo canal geram clipes repetidos, e ninguém vê de fora o que cada máquina está fazendo. Os limites são fixos (3 CPUs, `mem_limit: 8g` acima dos ~2,6 GB livres do PC de piso, `OMP_NUM_THREADS=6` na imagem), e o vigia de temperatura lê 0 °C no PC de piso: o host não tem `thermal_zone*`, o glob fica literal e o `cat` cai no `echo 0`, então o vigia nunca pausou nada nos 3 desligamentos. E a live só é clipada por tempo fixo (`--live-minutes`), não até acabar.

## Goals

- [ ] Uma live tem no máximo um PC dono por vez: 0 janelas processadas por dois PCs no teste de corrida com o fake.
- [ ] Painel na Vercel mostra cada PC (por apelido) como online/offline em <= 60 s após mudar, com CPU, RAM, disco, temperatura, GPU e job atual, e tem o botão "liberar recursos".
- [ ] Limite de lives contínuas por PC fixado a partir de medição curta em cada PC, não de chute.
- [ ] Vigia térmico lê o Tctl real do Ryzen 5500U (`k10temp`) e nunca registra 0 °C como leitura.
- [ ] `clipar <url> --ate-acabar` clipa a live em janelas até ela acabar, cada instante pertencendo a uma única janela, sem passar do piso de disco livre.

## Out of Scope

| Feature | Reason |
| ------- | ------ |
| Imagem com GPU (CUDA, `int8_float16` na GTX 1660 SUPER, NVENC) | Onda D: só desenho (`design.md` seção Onda D); campo `gpu` já vai no heartbeat |
| Mover vídeo entre PCs ou guardar vídeo no Mongo | Atlas free tem 0,5 GB e 10 GB/semana de tráfego; quem grava processa |
| Regravar janelas perdidas quando o PC dono cai | Janelas do dono morto viram `lost`; reprocessar via VOD fica para depois |
| Ações no painel além de "liberar recursos" (cancelar job, editar policy) | Só o comando de nível crítico foi pedido (rodada 2) |
| Túnel (Tailscale/Cloudflare) | Rodada 2: painel na Vercel, sem túnel |
| Deploy do painel na Vercel e criação dos usuários do Atlas | Feitos pelo usuário (deploy e banco fora do alcance do agente); o plano entrega código, `vercel.json` e guia |
| Rodar contra Mongo real em teste ou por agente | Regra global: nenhum agente acessa banco; testes com `mongomock`/fake em memória |
| Instalar Docker Desktop/WSL2 no Windows | Passo manual do usuário, com guia no README |
| Change streams, TTL, transações | Polling basta; mongomock não suporta TTL nem sessões |

---

## Assumptions & Open Questions

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --------------------- | -------------- | --------- | ---------- |
| "Simultâneas" | Vários canais por PC e vários PCs | Default do PEDIDO | n |
| Unidade de exclusão | A live (`lives._id = <plataforma>:<canal>:<stream_id>`), com lease de dono; jobs com `_id = <live_id>:<fim_da_janela>` | Relatório B: dono por transmissão resolve duplicata na raiz; `_id` único é o índice pedido, de graça | n |
| Onde fica o Mongo | MongoDB Atlas free (M0) criado pelo usuário; URI só em `CLIPES_MONGO_URI` no arquivo de segredos | 3 PCs alcançam; uso < 1% dos limites (100 ops/s, 0,5 GB) | n |
| IP access list do Atlas | `0.0.0.0/0` com senhas fortes; dois usuários de banco: `workers` (readWrite em `lives`, `jobs`, `workers`, `commands`) e `painel` (read em `lives`, `jobs`, `workers` + insert/update só em `commands`) | Rodada 2: Vercel sem IP fixo; permissão mínima no painel | y |
| Sem Mongo configurado ou Mongo fora do ar | `DiskQueue` + `flock` atuais (modo 1 PC); jobs continuam locais | Não quebra uso local; Mongo só coordena | n |
| Identidade do PC | `worker_id` UUID gerado 1x em `OUT_DIR/.worker_id`; painel mostra `CLIPES_ALIAS` (ex.: `PC-1`), nunca hostname/IP | Restrição: sem hostnames em log/painel | n |
| Lease e heartbeat | `CLIPES_LEASE_S=90`, heartbeat a cada `CLIPES_BEAT_S=20`; offline = sem heartbeat há > 60 s | 4 batidas antes de expirar; calibrável | n |
| Relógio | Relógio do cliente (PCs com NTP) | `$$NOW` no mongomock não verificado; deriva irrelevante para lease de 90 s | n |
| Preferir PC forte | Antes de reivindicar live sem dono, esperar `30 s / capacidade.score` | Sem escalonador central | n |
| Painel | Projeto `painel/` no repo para a Vercel: `index.html` estático + funções Python (`api/status.py`, `api/comando.py`) com `BaseHTTPRequestHandler` e `pymongo`; Basic Auth com senha em env da Vercel (`PAINEL_SENHA`), `secrets.compare_digest`; URI do usuário `painel` em env da Vercel | Rodada 2: Vercel lendo Mongo, sem túnel, auth no app; Python reaproveita `pymongo` e os testes com `mongomock` | y |
| Liberar recursos | Botão grava `commands` (`{worker_id, acao: "liberar", nivel: "critico", status: "pending"}`) após confirmação; o monitor do PC lê a cada heartbeat, mata os subprocessos do clipes-lives (ffmpeg, streamlink, llama-server), sai com código 75, e o `clipar` no host espera 60 s e reinicia o ciclo | Contêiner não tem socket do Docker; sair e deixar o `clipar` reiniciar cumpre "parar e reiniciar o ciclo" sem privilégio extra | n |
| Temperatura no contêiner | `vigia-temp` grava o valor atual em `$OUT_DIR/.temp` (`78` ou `sem-sensor`); o contêiner lê `/out/.temp` | Contêiner não precisa de acesso a `/sys/class/hwmon` | n |
| Sem sensor | Só avisar: vigia registra `sem-sensor`, `clipar` imprime aviso, painel mostra `n/d` em destaque | Rodada 2 | y |
| Limiar térmico | Mantém `CLIPES_TEMP_MAX=85`/`MIN=75` (env); Tctl ocioso já em ~79 °C, pode pausar muito | Pergunta aberta ao usuário | n |
| Perfil automático | `CLIPES_CPUS = max(1, nproc // 4)` em laptop (bateria presente) ou RAM livre < 4 GB; senão `nproc // 2`; `CLIPES_MEM = min(8g, 60% de MemAvailable)` | 5500U → 3 (seguro de hoje); 5700X → 8 | n |
| Threads dentro do contêiner | Função única `cpu_threads()` = `floor(quota/period)` de `/sys/fs/cgroup/cpu.max` (ou `CLIPES_CPU_THREADS`), usada por ASR, LLM, embeddings e `OMP_NUM_THREADS` | Hoje defaults de 6 threads em `asr.py:64,174`, `llm.py:228`, `topics.py:86` sobre-assinam a cota de 3 | n |
| Live contínua | `--ate-acabar`; janela de 10 min a cada `STEP=5 min` alinhada ao relógio (`floor(t/STEP)*STEP`); região dona do centro do clipe `[e − STEP − pad, e − pad]`, `pad = max_dur/2` | AD-002; `_id` do job reprodutível em qualquer PC | n |
| Fim da live | Encerrar quando 2 sinais concordarem (streamlink saiu, sem segmento novo há 90 s, Helix/`is_live` falso) ou 1 persistir por 5 min | Cobre queda e reconexão do streamer | n |
| Disco e backlog | Não abre janela nova com `free < CLIPES_MIN_FREE_GB` (5); backlog > 2 janelas descarta a mais antiga como `lost` | PC de piso com 15-16 GB livres e root a 94% | n |
| Lives contínuas por PC | `max_lives = max(1, floor(0,6 / razão))`, razão = segundos de processamento / segundos de live medidos por bench curto (`clipes bench-live`, 5 min de live já gravada) em cada PC, gravada em `OUT_DIR/.perfil.json`; sem medição = 1 | Rodada 2: investigar com números; medido no 5500U: razão ~0,33 → 1 live (2 dariam 0,66, sem folga para pausa térmica) | y |
| PCs | PC1 Linux 5500U (16 GB livres); PC2 Win 11 5700X 16t, 15,9 GB, GTX 1660 SUPER, 648 GB; PC3 Win 11 Pro i7-13620H 16t, 15,7 GB, RTX 4050 Laptop, 17 GB livres (notebook, risco térmico); PC2 e PC3 precisam de Docker Desktop + WSL2 | Rodada 2 | y |
| Disco por live | `live_source` ~1,1 GB por 20 min (~3,3 GB/h); `source.mkv` de cada janela apagado logo após processar | Rodada 2: PC1 16 GB e PC3 17 GB livres | y |

**Open questions:** none - all resolved or logged above (required before the spec is confirmed). Ainda com default aplicado: limiar térmico 85/75 com Tctl ocioso ~79 °C.

---

## User Stories

### P1: Vigia de temperatura corrigido ⭐ MVP

**User Story**: Como dono do PC de piso, quero que o vigia leia a temperatura real e pause o contêiner antes do desligamento.

**Why P1**: Segurança física; vem antes de qualquer carga nova.

**Acceptance Criteria**:

1. WHEN o vigia ler temperaturas THEN o sistema SHALL usar o maior `temp*_input / 1000` dos diretórios `$SYSFS_ROOT/class/hwmon/hwmon*` cujo `name` seja `k10temp`, `zenpower`, `coretemp` ou `cpu_thermal`, com `SYSFS_ROOT=/sys` por padrão.
2. IF nenhum hwmon de CPU existir THEN o sistema SHALL tentar `thermal_zone*/temp` legíveis e, sem nenhum, registrar `sem-sensor` no log e em `$OUT_DIR/.temp`, sem nunca registrar `0C`.
3. WHEN a temperatura for >= `CLIPES_TEMP_MAX` THEN o sistema SHALL pausar os contêineres do projeto, e WHEN cair a <= `CLIPES_TEMP_MIN` SHALL retomá-los.
4. The system SHALL sobrescrever `$OUT_DIR/.temp` a cada leitura com só o valor inteiro em °C ou `sem-sensor`.
5. WHEN `clipar` iniciar e `vigia-temp --check` imprimir `sem-sensor` THEN o sistema SHALL imprimir `aviso: sem sensor de temperatura; vigia termico desligado` e seguir sem mudar limites.

**Independent Test**: Com `SYSFS_ROOT` falso: `k10temp/temp1_input=86000` faz o `docker` falso receber `pause`; só `nvme` e sem `thermal_zone` grava `sem-sensor` em `.temp` e o `clipar` só avisa.

---

### P1: Capacidade do PC e limites automáticos ⭐ MVP

**User Story**: Como dono dos PCs, quero que cada máquina descubra núcleos, RAM, disco e GPU e ajuste os limites, em vez do fixo 3 CPUs.

**Why P1**: O 5700X fica subaproveitado e o 5500U não pode passar do seguro; a sobre-assinatura de threads existe hoje.

**Acceptance Criteria**:

1. WHEN o `clipar` iniciar sem `CLIPES_CPUS` THEN o sistema SHALL exportar `CLIPES_CPUS = max(1, nproc // 4)` se houver `/sys/class/power_supply/BAT*` ou `MemAvailable` < 4 GB, e `max(1, nproc // 2)` caso contrário.
2. WHEN o `clipar` iniciar sem `CLIPES_MEM` THEN o sistema SHALL exportar `CLIPES_MEM` = menor entre 8 GB e 60% de `MemAvailable`, em MB inteiros com sufixo `m`.
3. WHERE `CLIPES_CPUS`, `CLIPES_MEM` ou `CLIPES_CPU_THREADS` estiverem definidos the system SHALL usar o valor do usuário sem recalcular.
4. WHEN `cpu_threads()` rodar no contêiner THEN o sistema SHALL devolver `CLIPES_CPU_THREADS` se definido, senão `max(1, floor(quota/period))` de `/sys/fs/cgroup/cpu.max`, senão `os.cpu_count()`.
5. The system SHALL usar `cpu_threads()` como default de threads em ASR, LLM local e embeddings, no lugar do literal 6.
6. WHEN `capacity()` rodar THEN o sistema SHALL devolver `cpus` (= `cpu_threads()`), `ram_gb` (= menor entre `memory.max` e `MemAvailable`), `disco_livre_gb` de `OUT_DIR`, `temp_c` lido de `/out/.temp` (`None` se `sem-sensor` ou ausente), `gpu` (nome, VRAM, uso e temperatura via `nvidia-smi`, ou `None`) e `score = cpus * (2 se gpu senão 1)`.
7. IF `nvidia-smi` não existir ou falhar THEN o sistema SHALL devolver `gpu=None` sem exceção.

**Independent Test**: `cpu.max="300000 100000"` falso → `cpu_threads()==3`; `nproc=12` com bateria → `CLIPES_CPUS=3`; `nproc=16` sem bateria e 12 GB livres → 8.

---

### P1: Coordenação entre PCs sem duplicata ⭐ MVP

**User Story**: Como dono de 3 PCs, quero que todos vigiem lives ao mesmo tempo sem dois gravarem a mesma transmissão.

**Why P1**: Base do painel, da live contínua e da publicação.

**Acceptance Criteria**:

1. WHEN um PC detectar uma live candidata THEN o sistema SHALL criar o documento `lives` com `update_one({_id}, {$setOnInsert: ...}, upsert=True)` e tratar `DuplicateKeyError` como "já existe".
2. WHEN um PC quiser gravar uma live THEN o sistema SHALL reivindicá-la com um único `find_one_and_update` cujo filtro exige `status="live"` e (`owner` nulo, `lease_until < agora` ou `owner = worker_id`), gravando `owner` e `lease_until = agora + CLIPES_LEASE_S`.
3. IF o `find_one_and_update` devolver `None` THEN o sistema SHALL não gravar a live.
4. WHILE um PC for dono de uma live the system SHALL renovar o lease a cada `CLIPES_BEAT_S` com filtro `{_id, owner: worker_id}`.
5. IF a renovação casar 0 documentos THEN o sistema SHALL parar o gravador dessa live, registrar `[fila] lease perdida <live_id>` e não marcar jobs dela como `done`.
6. WHEN uma janela for fechada THEN o sistema SHALL inserir o job com `_id = <live_id>:<fim_da_janela>` e ignorar `DuplicateKeyError`.
7. WHEN um job terminar THEN o sistema SHALL atualizá-lo só com filtro `{_id, owner: worker_id, status: "processing"}`; repetir a conclusão SHALL não alterar o documento.
8. IF um job atingir `attempts >= 3` e a 3ª execução falhar (sem `done` ao tentar iniciar a 4ª vez) THEN o sistema SHALL marcá-lo `failed` (decisão: "até 3 tentativas" = 3 execuções reais em `start_job`, marcando `failed` só após falha da 3ª).
9. WHEN uma live sem dono for vista THEN o sistema SHALL esperar `30 s / capacity().score` antes de reivindicá-la.
10. WHERE `CLIPES_MONGO_URI` não estiver definido the system SHALL usar `DiskQueue` e `flock` atuais sem importar `pymongo`.
11. IF o Mongo ficar inacessível durante a execução THEN o sistema SHALL continuar o job atual, manter jobs novos na `DiskQueue` local, e tentar reconectar a cada 60 s.
12. The system SHALL ler a URI só do ambiente e nunca escrevê-la em log, `job.json`, painel ou mensagem de erro (`mongodb+srv://***@***`).
13. IF o lease de uma live vencer porque o dono caiu THEN o sistema SHALL marcar como `lost` os jobs `pending`/`processing` desse dono para a live, e o PC que reivindicar a live SHALL gravar a partir do instante atual, sem recriar jobs cujo `_id` já exista.

**Independent Test**: Com `mongomock`, worker A reivindica e B recebe `None`; relógio falso avança 91 s sem renovação de A e B reivindica; a renovação seguinte de A casa 0 documentos; concluir o mesmo job 2 vezes deixa 1 documento `done`.

---

### P2: Painel na Vercel e comando "liberar recursos"

**User Story**: Como dono dos PCs, quero abrir de qualquer lugar uma página com o status de cada máquina e, em emergência, mandar um PC liberar recursos.

**Why P2**: Observabilidade e segurança térmica remota; a coordenação funciona sem ele.

**Acceptance Criteria**:

1. WHEN o worker rodar THEN o sistema SHALL gravar a cada `CLIPES_BEAT_S` no documento `workers/<worker_id>` o `capacity()`, `alias`, `job_atual`, `heartbeat_at`, e `$push` em `history` com `$slice: -180`.
2. WHEN `GET /api/status` receber Basic Auth válida THEN a função SHALL responder 200 com JSON dos workers (`online = agora - heartbeat_at <= 3 * CLIPES_BEAT_S`), lives com dono e contagem de jobs por status nas últimas 24 h.
3. IF a Basic Auth faltar ou não casar (`secrets.compare_digest` sobre bytes UTF-8) THEN as funções SHALL responder 401 com `WWW-Authenticate: Basic` e sem dados.
4. IF `PAINEL_SENHA` ou `PAINEL_MONGO_URI` não estiverem definidas THEN as funções SHALL responder 500 com `painel nao configurado`, sem detalhe da causa.
5. The system SHALL escapar no HTML todo texto vindo do banco (alias, canal, título) e nunca mostrar URI, hostname ou IP.
6. WHEN `POST /api/comando` receber Basic Auth válida, `worker_id` existente e `confirmar=LIBERAR` THEN a função SHALL inserir em `commands` `{worker_id, acao: "liberar", status: "pending", criado_em}` e responder 202; sem `confirmar=LIBERAR` SHALL responder 400.
7. IF já existir comando `pending` para o mesmo `worker_id` THEN a função SHALL responder 409 sem inserir outro.
8. WHEN o monitor do PC encontrar comando `pending` para seu `worker_id` no heartbeat THEN o sistema SHALL marcá-lo `executando`, encerrar os subprocessos do clipes-lives (gravadores, ffmpeg, llama-server), marcar o comando `feito` com `executado_em`, registrar `[comando] liberar recursos` no log e sair com código 75.
9. WHEN o processo do contêiner sair com código 75 THEN o `clipar` SHALL esperar `CLIPES_RESTART_S` (60) e iniciar o ciclo de novo com os mesmos argumentos; qualquer outro código SHALL encerrar o `clipar` como hoje.
10. The system SHALL aceitar somente a ação `liberar` (lista branca); outra `acao` SHALL ser marcada `rejeitado` sem efeito.

**Independent Test**: Funções do painel com `mongomock`: sem auth 401; com auth 200 e worker com batida de 61 s atrás `online=false`; `POST` sem confirmação 400, com confirmação 202, repetido 409. Monitor com comando falso `pending` sai com 75 e marca `feito`; `clipar` com `docker` falso que sai 75 roda 2 ciclos.

---

### P2: Limite de lives por PC medido

**User Story**: Como dono dos PCs, quero que o número de lives simultâneas de cada PC venha de uma medição nele, para não sobrecarregar o notebook nem subaproveitar o desktop.

**Why P2**: Rodada 2 pediu números antes de fixar o limite.

**Acceptance Criteria**:

1. WHEN `clipar bench-live <pasta_de_job>` rodar THEN o sistema SHALL processar uma janela de live já gravada (>= 5 min) com o pipeline normal e gravar em `OUT_DIR/.perfil.json` `{razao, segundos_live, segundos_proc, medido_em, cpus}`.
2. The system SHALL calcular `max_lives = max(1, floor(0,6 / razao))` a partir de `.perfil.json`, e usar 1 quando o arquivo não existir ou `cpus` mudou desde a medição.
3. IF o PC já for dono de `max_lives` lives THEN o sistema SHALL não reivindicar outra.
4. The system SHALL publicar `max_lives` e `razao` no heartbeat para o painel.

**Independent Test**: Pipeline falso que leva 100 s para 300 s de live → `razao≈0,333`, `max_lives=1`; 30 s → `0,1`, `max_lives=6`; sem arquivo → 1.

---

### P2: Clipar a live até ela acabar

**User Story**: Como dono do canal, quero passar a URL de uma live e deixar clipando até ela terminar.

**Why P2**: Pedido item 4; hoje só tempo fixo.

**Acceptance Criteria**:

1. WHEN `clipar <url> --ate-acabar` receber uma live ativa THEN o sistema SHALL gravar em segmentos com o gravador do monitor e, a cada `STEP` (300 s, alinhado a `floor(t/STEP)*STEP`), congelar a janela `[t − 600 s, t]` e enfileirá-la como job.
2. The system SHALL manter de cada janela `[s, e]` só clipes cujo centro caia em `[e − STEP − pad, e − pad]`, com `pad = max_dur / 2`, e a última janela da live SHALL ser dona de `[e − STEP − pad, e]`.
3. IF um clipe tiver sobreposição >= 50% com um intervalo absoluto já emitido na mesma live (`emitted.json`) THEN o sistema SHALL descartá-lo.
4. WHEN dois sinais de fim concordarem (gravador saiu, nenhum segmento novo há 90 s, `is_live` falso) ou um deles persistir por 300 s THEN o sistema SHALL fechar a última janela (se >= 25 s), marcar a live `ended`, soltar o lease e sair após processar a fila da live.
5. IF o disco livre em `OUT_DIR` for menor que `CLIPES_MIN_FREE_GB` THEN o sistema SHALL não abrir a próxima janela e registrar `[live] janela pulada: disco`.
6. IF houver mais de 2 janelas esperando processamento THEN o sistema SHALL marcar a mais antiga como `lost` e apagar seu `source.mkv`.
7. WHERE o monitor rodar com `--follow <canal>` the system SHALL aplicar o mesmo laço por janelas só a esse canal.

**Independent Test**: Janelas sintéticas com candidatos nas bordas: cada pico emitido exatamente uma vez; gravador falso que para + sem segmentos por 90 s encerra; `disk_usage` falso com 4 GB pula a janela.

---

## Edge Cases

- IF o `.worker_id` não existir THEN o sistema SHALL gerá-lo com `uuid4().hex[:12]` prefixado por `w-` na 1ª execução.
- IF o contêiner rodar no Windows (WSL2) sem `/out/.temp` THEN o sistema SHALL reportar `temp_c=None` e o painel SHALL mostrar `n/d`.
- WHEN `stream_id` não vier do Helix THEN o sistema SHALL usar o `id` do `yt-dlp --dump-json` da live.
- IF o PC dono de uma live cair THEN o sistema SHALL, no outro PC, reivindicar após o lease vencer e gravar dali em diante; os jobs pendentes do dono antigo SHALL ser retomados só por ele (`owner == worker_id`).
- The system SHALL ter `sleep` >= 1 s em todo laço que toca o Mongo (limite de 100 ops/s do Atlas free).

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| -------------- | ----- | ----- | ------ |
| TEMP-01 | P1: Vigia (AC 1 a 4) | Tasks | Pending |
| TEMP-02 | P1: Vigia (AC 5) fallback no clipar | Tasks | Pending |
| CAP-01 | P1: Capacidade (AC 1 a 3) perfil no host | Tasks | Pending |
| CAP-02 | P1: Capacidade (AC 4, 5) `cpu_threads` único | Tasks | Pending |
| CAP-03 | P1: Capacidade (AC 6, 7) `capacity()` | Tasks | Pending |
| FILA-01 | P1: Coordenação (AC 1 a 5, 9, 13) lives, lease e reatribuição | Tasks | Pending |
| FILA-02 | P1: Coordenação (AC 6 a 8) jobs idempotentes | Tasks | Pending |
| FILA-03 | P1: Coordenação (AC 10 a 12) fallback local e segredo | Tasks | Pending |
| PAINEL-01 | P2: Painel (AC 1) heartbeat | Tasks | Pending |
| PAINEL-02 | P2: Painel (AC 2 a 5) funções Vercel e auth | Tasks | Pending |
| CMD-01 | P2: Painel (AC 6, 7, 10) comando liberar | Tasks | Pending |
| CMD-02 | P2: Painel (AC 8, 9) execução no PC e reinício | Tasks | Pending |
| PERF-01 | P2: Limite medido (AC 1) bench | Tasks | Pending |
| PERF-02 | P2: Limite medido (AC 2 a 4) max_lives | Tasks | Pending |
| LIVE-01 | P2: Live (AC 2, 3) região dona e emitidos | Tasks | Pending |
| LIVE-02 | P2: Live (AC 1, 4 a 6) laço, fim, disco, backlog | Tasks | Pending |
| LIVE-03 | P2: Live (AC 7) `--follow` no monitor | Tasks | Pending |

**Coverage:** 17 total, 17 mapped to tasks, 0 unmapped.

---

## Success Criteria

- [ ] Teste de corrida (fake) com 2 workers: 0 lives com 2 donos, 0 jobs duplicados, conclusão idempotente.
- [ ] No PC de piso, `.temp` mostra o Tctl real (observação passiva, sem carga) e o teste com sysfs falso pausa a 85 °C.
- [ ] Painel publicado pelo usuário na Vercel abre no celular com Basic Auth, mostra os PCs ligados e o botão liberar derruba e reinicia o ciclo de um PC (teste do usuário).
- [ ] `.perfil.json` medido em cada PC antes de subir `max_lives` acima de 1.
- [ ] Teste puro de janelas: cada pico sintético emitido exatamente 1 vez.
