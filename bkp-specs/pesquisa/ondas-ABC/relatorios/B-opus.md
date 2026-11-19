# Relatório B (Opus): fila Mongo multi-PC, dashboard, métricas, auto-ajuste e clipagem contínua

Escopo: itens 2, 2a, 2b e 4 do `PEDIDO.md`. Só pesquisa: nada foi implementado, commitado nem executado contra banco. Linhas de código citadas referem-se à worktree `pesq-opus` (HEAD `c19c1f4`). "NÃO VERIFICADO" = não achei fonte primária.

## 0. Resumo das decisões recomendadas

| Item | Decisão |
|---|---|
| 2 Fila | **pymongo síncrono**, 3 coleções (`lives`, `jobs`, `workers`). A unidade de exclusão é a **live** (um dono por transmissão, com lease). `jobs` com `_id` determinístico `<live_id>:<janela>` = índice único gratuito. `DiskQueue` local continua como está, e o Mongo só coordena. |
| 2 Testes | `mongomock` injetado no lugar do `MongoClient` (só no container de teste). Plano B: fake próprio de ~80 linhas se o mongomock falhar em algum operador. |
| 2a Dashboard | `http.server` da stdlib (sem dependência nova), página HTML única com auto-refresh, escutando só em `127.0.0.1`, atrás de **Tailscale (tailnet privada)** ou **Cloudflare Tunnel + Access**, mais Basic Auth na app com `secrets.compare_digest`. |
| 2a Métricas | Snapshot + histórico curto (`$push` com `$slice`) no próprio doc `workers`. CPU/RAM/disco via `/proc`, `/sys/fs/cgroup` e `shutil.disk_usage` (psutil é opcional). GPU via `nvidia-smi --query-gpu`. Temperatura lida do log do `vigia-temp`, que já cai em `OUT_DIR`. |P
| 2a vigia-temp | **Causa raiz confirmada**: este PC não tem `thermal_zone`. O glob não expande, o `cat` falha e o script cai em `echo 0`. Correção: ler `/sys/class/hwmon/*/temp*_input` dos chips de CPU (`k10temp`, `coretemp`, `zenpower`) e registrar "sem sensor" em vez de 0. |
| 2b Capacidade | Detectar no **host** (no script `clipar`) para escolher `CLIPES_CPUS`/`mem_limit`. Dentro do container, derivar threads de `cpu.max`. Uma função única `cpu_threads()` usada por ASR, LLM e embeddings. Override por env sempre vale. |
| 2b Preferir PC forte | Atraso de claim inversamente proporcional ao `capacity.score`, sem escalonador central. |
| Onda D (só desenho) | `Dockerfile.gpu` sobre `nvidia/cuda:12.x-cudnn9-runtime`, `device`/`compute_type` por env, `int8_float16` na GTX 1660 SUPER com teste de fumaça e fallback. |
| 4 Live contínua | Reaproveitar o gravador segmentado do monitor (`streamlink \| ffmpeg -f segment`) para `clipar <url> --ate-acabar`: janela de 10 min a cada 5 min (AD-002), região "dona" por janela para não repetir clipe, fim de live por saída do streamlink + falta de segmentos + Helix/yt-dlp. |

---

## 1. Fila no MongoDB para vários PCs (item 2)

### 1.1 O que existe hoje
- Fila em disco com `os.replace` entre `pending/processing/done/rejected` (`clipes/monitor.py:650-718`), claim pelo maior `z_score` (`monitor.py:671-691`), órfãos voltam a `pending` no boot (`monitor.py:702-713`).
- Exclusão só local: `flock` em `.worker.lock` (`monitor.py:790-800`). Dois PCs não se enxergam.
- O job depende de arquivo **local**: `source.mkv` (~200 MB, comentário em `monitor.py:699`) é gerado no PC que gravou (`monitor.py:607`).

### 1.2 Consequência de desenho (a mais importante)
O vídeo não pode viajar pelo Mongo: o Atlas free tem **0,5 GB** de armazenamento e **10 GB in/10 GB out por 7 dias** ([Atlas free limits](https://www.mongodb.com/docs/atlas/reference/free-shared-limitations/)). Logo:
- **Quem grava, processa.** O Mongo coordena *quem grava qual live*. Não é uma fila de vídeos.
- Clipe repetido entre PCs só acontece se dois PCs gravarem a mesma live. Basta garantir **um dono por transmissão** (lease no doc `lives`). Dedup fino entre janelas da mesma live é problema local do dono (seção 4.4).
- A `DiskQueue` atual fica como camada local (e como fallback offline se o Mongo cair).

### 1.3 Modelo de documentos (exemplo fictício)
```js
// lives: 1 doc por transmissão; _id único = dedup entre PCs
{ _id: "twitch:canal_exemplo:123456789",      // plataforma:canal:stream_id
  channel: "canal_exemplo", platform: "twitch", stream_id: "123456789",
  status: "live" | "ended" | "blocked",
  owner: "w-3f9a..." | null,                   // worker_id aleatório, NUNCA hostname
  lease_until: ISODate("2026-11-17T22:00:90Z"),
  next_window: 1763416800,                     // epoch alinhado ao passo (ver 4.3)
  created_at: ISODate(...), ended_at: null }

// jobs: 1 doc por janela; _id determinístico = idempotência
{ _id: "twitch:canal_exemplo:123456789:1763416800",
  live_id: "twitch:canal_exemplo:123456789", window_start: 1763416200, window_end: 1763416800,
  status: "pending" | "processing" | "done" | "rejected" | "lost" | "failed",
  owner: "w-3f9a...", lease_until: ISODate(...), attempts: 1, priority: 4.2,
  result: { clips: 3, score: 0.81, timings: {...} }, updated_at: ISODate(...) }

// workers: 1 doc por PC (status + métricas, ver 2.3)
{ _id: "w-3f9a...", alias: "PC-1",            // alias escolhido pelo usuário
  heartbeat_at: ISODate(...), capacity: { cpus: 3, ram_gb: 9, gpu: null, score: 1.0 },
  current: { live_id: "...", job_id: "..." }, metrics: {...}, history: [ ... ] }
```
- O `stream_id` da Twitch vem do campo `id` do Helix Get Streams. O código já chama esse endpoint (`monitor.py:88-112`), mas hoje só guarda `user_login` (`monitor.py:108`): é preciso guardar também o `id`. Sem credenciais Helix, usar o `id` do `yt-dlp --dump-json` (`ingest.py:68`, `ingest.py:482`). Se esse id é estável por transmissão no extractor da Twitch: **NÃO VERIFICADO**.
- `worker_id`: UUID gerado uma vez e salvo em `OUT_DIR/.worker_id`. Atende à restrição "nunca hostnames" do PEDIDO.

### 1.4 Claim atômico, lease, heartbeat e reclaim
Base: escrita em um único documento é atômica no MongoDB e "each command ensures the query condition still matches" ([atomicity](https://www.mongodb.com/docs/manual/core/write-operations-atomicity/)). `findOneAndUpdate` aceita `sort` e `returnDocument: "after"` ([findOneAndUpdate](https://www.mongodb.com/docs/manual/reference/method/db.collection.findOneAndUpdate/)).

```python
# claim de uma live (pymongo; esboço, não implementado)
now = utcnow()
doc = lives.find_one_and_update(
    {"_id": live_id, "status": "live",
     "$or": [{"owner": None}, {"lease_until": {"$lt": now}}, {"owner": me}]},
    {"$set": {"owner": me, "lease_until": now + LEASE}},
    return_document=ReturnDocument.AFTER)
# doc None => outro PC é dono; não gravar.
```
- **Criar a live**: `update_one({"_id": live_id}, {"$setOnInsert": {...}}, upsert=True)`. Com `_id` como chave, um upsert concorrente perdedor recebe `DuplicateKeyError` (11000), que é tratado como "já existe". A doc recomenda índice único no filtro do upsert ([findOneAndUpdate, upsert](https://www.mongodb.com/docs/manual/reference/method/db.collection.findOneAndUpdate/)), e o `findAndModify` não faz retry em violação de índice único ([SERVER-60252](https://jira.mongodb.com/browse/SERVER-60252)).
- **Heartbeat com fencing**: `update_one({"_id": live_id, "owner": me}, {"$set": {"lease_until": now+LEASE}})`. Se `matched_count == 0`, o lease foi perdido: parar de gravar e não marcar `done`.
- **Valores**: `LEASE = 90 s`, heartbeat a cada 20 s (≥ 4 batidas antes de expirar). Ficam em env, como botão de calibração.
- **Reclaim**: implícito no `$lt: now` do claim. Se o PC dono morre, outro PC assume a live e grava **daqui para frente**. Janelas antigas do dono morto viram `lost` (o vídeo está no disco dele). Quando ele volta, `recover_orphans` (`monitor.py:702`) só pode retomar os jobs cujo `owner == me`.
- **Jobs**: criados com `insert_one({"_id": ...})`, ignorando `DuplicateKeyError`. Fechados com `update_one({"_id": id, "owner": me, "status": "processing"}, {"$set": {"status": "done", ...}})`. Idempotente: repetir não duplica. Limite `attempts < 3`, depois `failed`.
- **Relógio**: os leases usam o relógio do cliente. Com 3 PCs em NTP e lease de 90 s, a deriva é irrelevante. Usar `$$NOW` do servidor (pipeline de update) eliminaria isso, mas o suporte no mongomock é **NÃO VERIFICADO**, então não recomendo agora.

### 1.5 Índices
- `lives._id` e `jobs._id` já são únicos: são o "índice único por canal+transmissão+janela" pedido, sem índice extra.
- `jobs`: `{status: 1, priority: -1}` para o dashboard e o claim local. `lives`: `{status: 1, lease_until: 1}`.
- TTL: dispensável (histórico fica no array com `$slice`). Se for usado, rodar só em campo `date` de índice simples, com o job de limpeza a cada 60 s ([TTL](https://www.mongodb.com/docs/manual/core/index-ttl/)). O mongomock não expira documentos ([Missing_Features](https://raw.githubusercontent.com/mongomock/mongomock/develop/Missing_Features.rst)), então nenhum teste pode depender de TTL.

### 1.6 Driver
- **pymongo síncrono**. O worker já roda em thread via `asyncio.to_thread` (`monitor.py:894`), e as chamadas ao Mongo são poucas e curtas. Dá para envolvê-las em `to_thread` sem precisar de driver async.
- **Motor descartado**: deprecado em 14/05/2025, fim de vida em 14/05/2026, só correções críticas até 14/05/2027 ([migração PyMongo](https://www.mongodb.com/docs/languages/python/pymongo-driver/reference/migration/)).
- **PyMongo Async** (`AsyncMongoClient`): substituto oficial do Motor (mesma fonte). Descartado por enquanto (YAGNI): não há volume que justifique, e o suporte do mongomock à API async é **NÃO VERIFICADO**.
- `maxPoolSize` pequeno (ex.: 5) por processo. O Atlas free aceita no máximo 500 conexões ([limits](https://www.mongodb.com/docs/atlas/reference/free-shared-limitations/)).

### 1.7 Limites do Atlas free e orçamento
Fonte única: [Atlas free limits](https://www.mongodb.com/docs/atlas/reference/free-shared-limitations/).
- **100 ops/s**. Acima disso há throttle com cooldown de 1 s por conexão. Estimativa: 3 PCs × (1 heartbeat de live + 1 de worker) / 20 s ≈ 0,3 ops/s, mais o dashboard a cada 10 s. Folga de mais de 100×.
- **0,5 GB** (BSON + índices). Job ≈ 1–2 KB (estimativa): 100 mil jobs ≈ 200 MB. Purga manual de jobs `done` com mais de 30 dias via script, e a query é entregue ao usuário (regra de banco).
- **10 GB in / 10 GB out por 7 dias**: irrelevante sem vídeo.
- **Pausa automática após 30 dias sem conexões**. Backup diário com até 4 snapshots, se ativado.
- 1 cluster free por projeto, 500 coleções, sem JS no servidor (`$where` proibido).
- **IP access list** do Atlas com IPs residenciais dinâmicos: liberar `0.0.0.0/0` com usuário/senha fortes ou manter a lista atualizada. Política exata do Atlas: **NÃO VERIFICADO**. É decisão do usuário (pergunta em 6).
- A connection string fica só em `.env` não versionado (checar se o `.gitignore` cobre). Nunca vai para spec nem log.

### 1.8 Testes sem Mongo real
- Injetar o client: `Coord(client=mongomock.MongoClient())` nos testes e `pymongo.MongoClient(os.environ["MONGO_URI"])` em produção. Sem `mongomock.patch`, para nada tentar abrir rede ([mongomock README](https://github.com/mongomock/mongomock)).
- O mongomock levanta `DuplicateKeyError` em índice único ([mirror da doc](https://tessl.io/registry/tessl/pypi-mongomock/files/docs/errors.md), não oficial). `find_one_and_update` aparece na suíte de testes do pacote ([Debian, test__collection_api.py](https://sources.debian.org/data/main/p/python-mongomock/4.3.0-4/tests/test__collection_api.py)). Combinação com `$or`/`$lt`/`$push`+`$slice`: **NÃO VERIFICADO**, então o primeiro teste da task deve cobrir isso.
- Sem sessões/transações ([Missing_Features](https://raw.githubusercontent.com/mongomock/mongomock/develop/Missing_Features.rst)). O desenho acima não usa nenhuma das duas.
- Corrida entre PCs: simular com dois "workers" (`me="A"`, `me="B"`) no mesmo client fake. Casos: A pega, B recebe None; A para de bater, o relógio fake avança 91 s e B pega; o heartbeat de A agora retorna `matched_count == 0`; fechar o mesmo job duas vezes não duplica. Concorrência real de threads no mongomock: **NÃO VERIFICADO**. Não é necessária, porque a atomicidade é responsabilidade do servidor real.
- Risco: o mongomock está "seeking maintainers" ([README](https://github.com/mongomock/mongomock)). Plano B: um fake próprio só com `find_one_and_update`, `update_one`, `insert_one` e `find` para os filtros usados.
- Dependências: `pymongo` na imagem; `mongomock` só no `pip install` de teste do `Dockerfile` (`Dockerfile:34-39` já instala `pytest` lá). Nada no host.

### 1.9 Alternativas descartadas
- **GridFS/vídeo no Mongo**: estoura 0,5 GB e 10 GB/semana.
- **Fila por clipe com índice único por intervalo de tempo**: o Mongo não tem unicidade por sobreposição de intervalos. A quantização por bucket gera falsos positivos/negativos nas bordas. Dono por live resolve na raiz.
- **Redis/Postgres**: o PEDIDO fixou Mongo.
- **Change streams para notificar workers**: polling a cada 20 s basta, e a filtragem de change streams no free tem restrições (mesma fonte).

---

## 2. Dashboard e status dos PCs (itens 2 e 2a)

### 2.1 Tecnologia mínima
- **Recomendado**: `http.server.ThreadingHTTPServer` da stdlib ([docs](https://docs.python.org/3/library/http.server.html)), um handler `GET /` que lê `workers`/`lives`/`jobs` e devolve HTML com `<meta http-equiv="refresh" content="10">`, mais `GET /api/status` em JSON. Zero dependência nova e ~100 linhas. Novo subcomando `python -m clipes painel`, roteado pelo `__main__.py`.
- A doc avisa que `http.server` "is not recommended for production" (mesma fonte). Aceitável aqui porque é só leitura, tem 1 usuário, escuta em `127.0.0.1` e fica atrás de túnel autenticado. Se crescer (comandos, POST), trocar por FastAPI.
- **FastAPI** (default do PEDIDO): bom se houver formulários/ações. Para status só de leitura é dependência a mais. Se for escolhido, usar `HTTPBasic` com `secrets.compare_digest` sobre bytes UTF-8 ([FastAPI HTTP Basic](https://fastapi.tiangolo.com/advanced/security/http-basic-auth/)).
- Qualquer PC pode rodar o painel, porque o estado está no Mongo. Recomendo o PC 2 (mais folga) ou o que ficar ligado.

### 2.2 Acesso de qualquer lugar (túnel + autenticação)
| Opção | Auth | Requisito | Veredito |
|---|---|---|---|
| **Tailscale (tailnet privada)** | Do dispositivo (só aparelhos do usuário na tailnet) | App Tailscale no celular/PCs | **Recomendado** se o usuário aceitar instalar o app. Detalhes do `tailscale serve`: **NÃO VERIFICADO** (só a página do Funnel foi lida). |
| **Cloudflare Tunnel + Access** | Login (e-mail OTP etc.) antes de chegar ao app | Domínio no Cloudflare ([Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/index.md)). Zero Trust grátis até 50 usuários ([planos](https://www.cloudflare.com/plans/sase-zero-trust.md)) | **Recomendado** se o usuário tiver ou quiser um domínio. |
| Tailscale Funnel | **Nenhuma**: público para qualquer um, portas 443/8443/10000, banda limitada ([Funnel](https://tailscale.com/kb/1223/funnel)) | – | Só com Basic Auth forte na app. Descartado como camada principal. |
| Quick Tunnel `trycloudflare.com` | Nenhuma, URL muda a cada execução, sem SLA, sem SSE ([Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/)) | – | Descartado (URL instável e pública). |

- Em qualquer opção: Basic Auth na app como 2ª camada, senha em `.env`, comparação com `compare_digest`.
- "Nada instalado no host": rodar `cloudflared`/`tailscale` como serviço extra no `docker-compose.yml`. Nomes/tags exatos das imagens oficiais: **NÃO VERIFICADO**.

### 2.3 Métricas por PC
O agente roda dentro do processo do monitor/clipar, como thread que bate a cada 20 s:
```js
$set: { heartbeat_at, metrics: { cpu_pct, load1, ram_used_gb, ram_avail_gb, disk_free_gb,
        temp_cpu_c | null, gpu: { util_pct, mem_used_mb, temp_c } | null, recorders: 2, job_id } },
$push: { history: { $each: [ {t, cpu_pct, temp_cpu_c} ], $slice: -180 } }   // ~1 h a cada 20 s
```
- **Online/offline**: offline se `heartbeat_at < agora − 3×20 s`, calculado na leitura. Sem TTL e sem coleção extra.
- **CPU/RAM**: `/proc/stat`, `/proc/meminfo`, `os.getloadavg()`. Dentro do container, `/proc/meminfo` mostra o host e não o limite do container (**NÃO VERIFICADO** como fonte), então também ler `/sys/fs/cgroup/memory.max` e `cpu.max` ([cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html)). psutil seria o atalho cross-platform ([psutil](https://psutil.io/)), mas é dependência nova para o que a stdlib faz em ~30 linhas. Adotar só se o Windows exigir.
- **Disco**: `shutil.disk_usage("/out")` (stdlib).
- **GPU**: `nvidia-smi --query-gpu=utilization.gpu,memory.used,temperature.gpu --format=csv,noheader,nounits` ([man nvidia-smi](https://www.mankier.com/1/nvidia-smi)). Ausente = `null`. Só faz sentido na imagem GPU (Onda D).
- **Temperatura da CPU**: o `vigia-temp` já roda no host e grava em `$HOME/Downloads/clipes-lives/.temp.log` (`vigia-temp:5`), que é exatamente o `OUT_DIR` montado em `/out` (`docker-compose.yml:18`). O container lê a última linha de `/out/.temp.log`, sem dar ao container acesso a `/sys/class/hwmon`. Recomendo o vigia passar a escrever também `/out/.temp` (só o valor atual) para não fazer parse de log.
- **Windows**: o container (WSL2) não vê sensores de CPU do Windows: **NÃO VERIFICADO**. Mostrar "indisponível" em vez de 0. `nvidia-smi` dentro do WSL2 com GPU-PV: **NÃO VERIFICADO** (o Docker documenta o `--gpus=all` no WSL2: [Docker GPU](https://docs.docker.com/desktop/features/gpu/)).

### 2.4 Conserto do vigia-temp (lê 0 °C)
Diagnóstico feito neste PC (leitura de `/sys`, sem carga):
- `/sys/class/thermal/thermal_zone*` **não existe**. Em `vigia-temp:10` o glob fica literal, o `cat` falha, o `|| echo 0` devolve 0 e `t` fica 0. O `.temp.log` real mostra só `0C pausado=0`. **O vigia nunca pausou nada**, o que é coerente com os 3 desligamentos sob carga.
- O sensor existe em hwmon: `hwmon3 name=k10temp temp1_label=Tctl temp1_input=79000` (79 °C), além de `amdgpu edge 63 °C` e `nvme 51,8 °C`.
- Kernel: no k10temp, Tctl é sempre `temp1_input` e "is a non-physical temperature on an arbitrary scale" (pode ter offset); Tdie (`temp2_input`) só existe em algumas CPUs ([k10temp](https://www.kernel.org/doc/html/v5.2/hwmon/k10temp.html)).

Correção proposta (esboço, não aplicado):
```sh
t=""
for h in /sys/class/hwmon/hwmon*; do
  case "$(cat "$h/name" 2>/dev/null)" in k10temp|zenpower|coretemp|cpu_thermal|acpitz) ;; *) continue;; esac
  for f in "$h"/temp*_input; do [ -r "$f" ] || continue
    v=$(( $(cat "$f") / 1000 )); [ -z "$t" ] || [ "$v" -gt "$t" ] && t=$v; done
done
[ -z "$t" ] && for z in /sys/class/thermal/thermal_zone*/temp; do [ -r "$z" ] && ...; done   # fallback antigo
[ -z "$t" ] && { echo "$(date +%T) sem-sensor pausado=$pausado" >> "$LOG"; sleep 5; continue; }
```
- Sem sensor = **desconhecido**, nunca 0. Opcional: no modo "sem sensor", pausar por segurança se `load1 > nproc` (decisão do usuário).
- Filtrar por nome evita usar `nvme`/`amdgpu`/`BAT0` como temperatura de CPU.
- **Atenção à calibração**: 79 °C de Tctl com o PC sem o pipeline rodando (havia outros processos) e `MAX=85` (`vigia-temp:4`) significam pausas frequentes assim que o vigia funcionar. Manter `CLIPES_TEMP_MAX/MIN` como botão. Validar se o 5500U aplica offset no Tctl: **NÃO VERIFICADO**.
- Teste: um script de teste com `SYSFS_ROOT` apontando para uma árvore falsa em `tmp_path` (hwmon com k10temp, sem thermal_zone, sem nada), conferindo a saída "79C", "sem-sensor" e o fallback. Exige parametrizar a raiz `/sys` no script.

---

## 3. Detecção de capacidade e auto-ajuste (item 2b)

### 3.1 Problema atual (achados no código)
- Cota fixa de 3 CPUs (`docker-compose.yml:7`, `clipar:4`), `CLIPES_CPU_THREADS=3` (`docker-compose.yml:15`) e `mem_limit: 8g` fixo (`docker-compose.yml:6`), **maior que os ~2,6 GB livres** do PC de piso.
- **Sobre-assinatura**: `OMP_NUM_THREADS=6` e `OPENBLAS_NUM_THREADS=6` na imagem (`Dockerfile:15-16`). O default de threads quando o env falta é 6 em `asr.py:64`, `asr.py:174`, `llm.py:228` e `topics.py:86`. O teste (`docker-compose.yml:21-34`) e qualquer caminho sem env rodam 6 threads numa cota de 3 CPUs.
- `device="cpu", compute_type="int8"` fixos em `asr.py:90-91` e `asr.py:194`. `libx264 -preset veryfast` sem `-threads` em `render.py:408`.
- `--max-recorders` limitado a 3 por código (`monitor.py:853`).

### 3.2 Recomendação
1. **No host** (o `clipar` já é o ponto de entrada, `clipar:1-11`): se `CLIPES_CPUS` não estiver definido, calcular com `nproc`, `/proc/meminfo` e `/sys/class/power_supply/BAT*` (laptop) e exportar `CLIPES_CPUS` e `CLIPES_MEM` antes do `docker compose run`. Override do usuário sempre ganha.
2. **No container**, uma única função `capacity()` num módulo pequeno:
   - `cpus = quota/period` de `/sys/fs/cgroup/cpu.max` ([cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html)). O `--cpus` do Docker é cota CFS e não restringe a afinidade ([Docker resource constraints](https://docs.docker.com/engine/containers/resource_constraints/)), então `os.cpu_count()`/`os.sched_getaffinity` enxergam o host todo. A imagem é Python 3.12 (`Dockerfile:1`). `os.process_cpu_count` é ≥ 3.13 (a docstring local confirma que é por afinidade; a versão de introdução é **NÃO VERIFICADO**) e também não lê cota.
   - `ram = min(memory.max, MemAvailable)`.
   - `gpu`: `nvidia-smi -L` presente, ou `ctranslate2.get_supported_compute_types("cuda")` ([quantização CTranslate2](https://opennmt.net/CTranslate2/quantization.html)).
   - `threads = max(1, floor(cpus))`, usado por `asr.py`, `llm.py` e `topics.py`, e exportado como `OMP_NUM_THREADS` no `entrypoint.sh`.
3. **Tabela de perfis** (valores iniciais, a calibrar; nenhum foi medido):

| Perfil | Critério | CPUS | Whisper | Gravadores simultâneos | LLM |
|---|---|---|---|---|---|
| piso (laptop) | bateria presente ou RAM livre < 4 GB | `nproc/4` (=3 no 5500U) | `small` int8 | 1 live contínua **ou** monitor com 2 quentes | Qwen3-1.7B |
| médio | desktop, RAM livre ≥ 8 GB | `nproc/2` (=8 no 5700X) | `small`; `large-v3-turbo` se RAM livre ≥ 6 GB (**NÃO VERIFICADO** o requisito exato) | 3 + 1 | 1.7B (4B se `WITH_LLM_4B=1`, `Dockerfile:45-56`) |
| GPU (Onda D) | NVIDIA presente | `nproc/2` | `large-v3-turbo` em CUDA | 3 + 1 | idem |

   - `nproc/4` no laptop vem do histórico: desligou 3 vezes com 6 threads (PEDIDO), e 3 é o padrão atual que se mantém.
4. `capacity.score` (ex.: `cpus × (2 se GPU)`) vai para o doc `workers`. **Preferir PC forte**: antes de dar claim numa live sem dono, o worker espera `base_delay / score` segundos (ex.: 30 s / score). O mais forte chega primeiro, e o fraco só pega se o forte estiver ocupado ou offline. Sem escalonador central.
5. Limite global por PC: no máximo N lives contínuas (`= perfil`). O claim é recusado localmente se o PC já tiver N lives.

### 3.3 Windows 11 (PC 2: Ryzen 7 5700X, 15,9 GB, GTX 1660 SUPER)
- Docker Desktop com backend WSL2. GPU só funciona nesse backend e só NVIDIA (GPU-PV) ([Docker GPU](https://docs.docker.com/desktop/features/gpu/)).
- Recursos da VM em `%UserProfile%\.wslconfig` `[wsl2]`. `memory` tem default de 50% da RAM e `processors` default de todos os lógicos. Aplicar com `wsl --shutdown` ([WSL config](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)). Sugestão: `memory=10GB`, `processors=12`, e dentro disso o `cpus:` do compose continua valendo.
- O `clipar` e o `vigia-temp` são `sh`, então rodar de dentro de uma distro WSL. `nice` e `/sys/class/hwmon` não refletem o host Windows: **NÃO VERIFICADO**. No Windows, o vigia térmico de CPU fica desativado e marcado "indisponível". Temperatura de GPU via `nvidia-smi`.
- Volumes em `/mnt/c` são lentos no WSL2: **NÃO VERIFICADO**. Recomendo `OUT_DIR` no filesystem da distro.

### 3.4 Onda D (só desenho): GPU
- Imagem: `Dockerfile.gpu` sobre `nvidia/cuda:12.x-cudnn9-runtime-ubuntu22.04`. O faster-whisper atual exige cuBLAS CUDA 12 + cuDNN 9, e o README sugere essa família de imagem ([faster-whisper README](https://raw.githubusercontent.com/SYSTRAN/faster-whisper/master/README.md)). Override `docker-compose.gpu.yml` com reserva de GPU; sintaxe exata do `deploy.resources.reservations.devices`: **NÃO VERIFICADO**.
- Código: `CLIPES_DEVICE` (`cpu`/`cuda`) e `CLIPES_COMPUTE_TYPE` substituem os literais de `asr.py:90-91,194`. Default continua `cpu/int8`.
- **GTX 1660 SUPER**: compute capability 7.5 ([fpsbench](https://fpsbench.com/compare/gpu/nvidia-geforce-gtx-1660-super-vs-nvidia-geforce-gtx-1660), [TechPowerUp TU116](https://techpowerup.com/gpu-specs/nvidia-tu116.g902); a lista oficial da NVIDIA não traz a 1660 SUPER, só a 1650 Ti em 7.5: [CUDA GPUs](https://developer.nvidia.com/cuda-gpus)). No CTranslate2, `float16` e `int8` exigem CC ≥ 7.0, e `bfloat16` exige ≥ 8.0 ([quantização](https://opennmt.net/CTranslate2/quantization.html)). O TU116 não tem Tensor Cores, mas tem unidades FP16 dedicadas com o dobro da taxa FP32 ([Tom's Hardware](https://www.tomshardware.com/reviews/nvidia-geforce-gtx-1660-turing-tu116,6027.html)).
  - **Recomendado**: `int8_float16` (menos VRAM). Na largada, rodar `get_supported_compute_types("cuda")` e transcrever 5 s de áudio fixo; se der NaN, vazio ou erro, cair para `int8_float32`, depois `float32`, e por fim CPU. Problemas de FP16 da série GTX 16 com CTranslate2: **NÃO VERIFICADO**.
  - VRAM: large-v2 int8 beam 5 = 2926 MB numa RTX 3070 Ti ([README](https://raw.githubusercontent.com/SYSTRAN/faster-whisper/master/README.md)). Que o large-v3-turbo em int8_float16 cabe em 6 GB é estimativa **NÃO VERIFICADA**.
- Render com NVENC (`h264_nvenc` no lugar de `libx264`, `render.py:408`): fora desta onda. Suporte no Docker Desktop: **NÃO VERIFICADO**.
- AMD: a Radeon integrada do PC 1 aparece como `amdgpu` no hwmon, mas o Docker Desktop só faz GPU-PV NVIDIA (fonte acima). CTranslate2 com ROCm existe ([AMD ROCm blog](https://rocm.blogs.amd.com/artificial-intelligence/ctranslate2/README.html)), mas não para iGPU de laptop em Docker. Descartado.

---

## 4. Clipagem contínua até a live acabar (item 4)

### 4.1 Hoje
- `clipar <url> --live-minutes N` grava `-t N*60` em um único `.mkv` e processa como VOD (`ingest.py:131-194`, `ingest.py:511-514`, `cli.py:38-39`). Chat de live é ignorado (`ingest.py:526-527`). AD-007 registrou isso; a AD-002 (janela de 10 min a cada 5 min) ficou para depois (`STATE.md`).
- O monitor já tem as peças: gravador segmentado `streamlink | ffmpeg -f segment` de 10 s (`monitor.py:351-361`), `freeze_and_concat` (`monitor.py:529-547`), retenção/teto de disco (`monitor.py:480-511`, `BUFFER_CAP_BYTES=1,5 GB` em `monitor.py:785`), IRC anônimo (`monitor.py:287-345`), `run_local_pipeline` via `ingest_local` (`monitor.py:721-742`) e `source.mkv` apagado após o job (`monitor.py:699`).

### 4.2 Recomendação: reaproveitar, não reescrever
Novo modo `clipar <url> --ate-acabar` (o `--live-minutes` continua). Um canal por execução, conforme o default do PEDIDO.
1. Gravar com `RollingRecorderPool` de 1 canal (Twitch). YouTube: `yt-dlp -g` + ffmpeg segment, como em `ingest.py:148-170`, com `-f segment` no lugar de `-t`.
2. A cada `STEP = 5 min` (alinhado ao relógio: `floor(t/STEP)*STEP`, o que torna o `_id` do job reprodutível em qualquer PC), congelar `[t−10 min, t]`, gerar `chat.json` pelo IRC (Twitch) e enfileirar na `DiskQueue` local. O worker sequencial existente processa.
3. `hold` da janela em processamento, como já existe (`monitor.py:513-517`). O resto do buffer segue a regra `cleanup` + `enforce_cap`.
4. O monitor por picos e o modo contínuo compartilham o gravador. Picos de chat durante a live contínua viram prioridade (`priority = z_score`) em vez de jobs separados, porque a janela já cobre o trecho.

### 4.3 Fim da live
Combinar sinais baratos e encerrar quando **dois** concordarem, ou quando um persistir por mais de `GRACE = 5 min` (cobre queda do streamer e reconexão):
- O processo `streamlink` termina (o código já checa com `RollingRecorderPool.alive`, `monitor.py:371-372`). O comportamento exato ao ficar offline (código de saída, mensagem) é **NÃO VERIFICADO**, então testar com `-l debug`.
- Nenhum segmento novo em mais de 90 s (`segments()`, `monitor.py:475-478`).
- Twitch com credenciais: Helix Get Streams sem o canal, respeitando `HELIX_MIN_INTERVAL_S = 60` (`monitor.py:29`). YouTube: `probe_metadata` → `is_live == False` (`ingest.py:68`, `ingest.py:486`).
- No fim: processar a última janela parcial (≥ `MIN_CLIP_SOURCE_S`, `monitor.py:47`), marcar a live `ended` no Mongo, soltar o lease e parar.

### 4.4 Dedup entre chunks (janelas sobrepostas)
- **Região dona**: a janela `[s, e]` só aceita clipes cujo centro caia em `[e − STEP − pad, e − pad]`, com `pad = max_dur/2`. Assim um clipe nunca é cortado na borda, e cada instante pertence a exatamente uma janela. A última janela (fim da live) é dona de `[e − STEP − pad, e]`.
- **Cinto de segurança**: lista local dos intervalos absolutos já emitidos (`emitted.json` por live). Descartar clipe com sobreposição ≥ 50% de um já emitido.
- Os tempos absolutos saem de `video_start_ts` (`monitor.py:525-527`) + offset do clipe.
- Teste puro (sem vídeo): sequência de janelas sintéticas com clipes candidatos nas bordas; esperar cada pico emitido exatamente uma vez.

### 4.5 CPU e disco (PC de piso: 16 GB livres, root com 94% de uso)
- **Gravar** é `-c copy` (`monitor.py:358`), quase sem CPU. O gargalo é processar. Regra: o tempo de processar uma janela tem de ficar **abaixo de STEP**. Medir com os `timings` que o pipeline já devolve (`monitor.py:778`). Se 2 janelas seguidas passarem de STEP, aumentar o STEP (5→10 min) ou baixar `--asr-budget-ratio` (cota existente, commit `0209722`), em vez de deixar o backlog crescer. Se o backlog passar de 2 janelas, descartar a mais antiga como `lost` e logar.
- **Disco**: o bitrate de 720p60 da Twitch não foi medido (~3–6 Mbps, **NÃO VERIFICADO**), o que dá cerca de 1,4–2,7 GB/h. O buffer necessário é janela + backlog ≈ 20 min, ou seja ≤ 1 GB, dentro do `BUFFER_CAP_BYTES` de 1,5 GB. Saídas: só os clipes finais. `source.mkv` e o WAV de cada janela são apagados após o job.
- **Guarda de disco nova**: se `shutil.disk_usage(OUT_DIR).free < 3 GB` (env), parar de abrir janelas novas e avisar no heartbeat/dashboard. Com 16 GB livres e root a 94%, sem isso o sistema do usuário pode encher.
- Temperatura: depende do vigia consertado (2.4). Com `docker pause`, a gravação também para e perde trecho da live. Aceitável e documentado como janela `lost`.

### 4.6 Alternativas descartadas
- **Gravar a live inteira e processar no fim**: disco (horas × GB/h) e nenhum clipe durante a live.
- **Janelas sem sobreposição**: perde picos na borda.
- **Dedup por similaridade de transcrição**: caro e frágil; o tempo absoluto basta.

---

## 5. Tarefas sugeridas (ordem serial, Onda B)

1. **B-T1 vigia-temp**: ler hwmon filtrado por nome, "sem sensor" ≠ 0, escrever `/out/.temp`, teste com sysfs falso. *Primeiro, porque é segurança física.*
2. **B-T2 capacidade**: `capacity()` + `cpu_threads()` única, usada em `asr.py`/`llm.py`/`topics.py` e no `OMP_NUM_THREADS`. Auto `CLIPES_CPUS`/`CLIPES_MEM` no `clipar`. Testes com `cpu.max`/`meminfo` falsos.
3. **B-T3 coordenação Mongo**: módulo com `claim_live`, `heartbeat`, `release`, `upsert_job`, `finish_job`, `worker_beat`; client injetado. Testes com mongomock (corrida A/B, lease expirado, fencing, idempotência). `pymongo` na imagem, `mongomock` só para teste. `MONGO_URI` só por env.
4. **B-T4 integração no monitor**: claim da live antes de gravar, `stream_id` do Helix, atraso de claim por `score`, fallback `DiskQueue` se o Mongo estiver fora.
5. **B-T5 painel**: `python -m clipes painel` (stdlib), Basic Auth com `compare_digest`, HTML + `/api/status`. Serviço de túnel no compose (opção escolhida pelo usuário).
6. **B-T6 live contínua**: `--ate-acabar`, janelas alinhadas, região dona + `emitted.json`, fim de live com GRACE, guarda de disco, controle de backlog.
7. **D-T0 (só desenho)**: `Dockerfile.gpu`, `CLIPES_DEVICE`/`CLIPES_COMPUTE_TYPE`, teste de fumaça de compute type.

## 6. Perguntas em aberto (para o usuário)
1. Túnel: **Tailscale** (instalar o app no celular) ou **Cloudflare Access** (precisa de domínio no Cloudflare)?
2. Atlas IP access list: aceitar `0.0.0.0/0` com senha forte, ou manter a lista com IPs residenciais (que mudam)?
3. Sem sensor de temperatura (Windows ou falha), o vigia deve **pausar por carga** ou só avisar?
4. Com o Tctl em 79 °C e o pipeline parado, manter `MAX=85/MIN=75` ou baixar (ex.: 80/70) no PC de piso?
5. Ao perder o dono de uma live (PC caiu), as janelas não processadas viram `lost` (proposta) ou vale tentar regravar/baixar o VOD depois?
6. Quantas lives contínuas simultâneas por PC no perfil "médio" (PC 2)? Proposta: 2.
7. Specs do PC 3 (Windows) para entrar na tabela de perfis.
8. Onde o painel fica ligado 24 h (qual PC)?

## 7. Riscos e limites
- **Atlas free**: 100 ops/s, 0,5 GB, 10 GB/semana, pausa após 30 dias sem conexão, sem SLA de free ([limits](https://www.mongodb.com/docs/atlas/reference/free-shared-limitations/)). O desenho usa menos de 1% de cada limite, mas um bug de loop sem `sleep` estoura as ops/s. Adicionar um `sleep` mínimo em todo laço que toca o Mongo.
- **Regra "nunca acessar banco"**: ninguém roda contra o Atlas durante o desenvolvimento. O primeiro uso real é do usuário. Consultas de diagnóstico são entregues como query para o usuário rodar (skill `mongo-coleta`).
- **mongomock** sem mantenedor e sem TTL nem sessões. Os testes não cobrem a atomicidade real do servidor (garantida pela doc do MongoDB, não por teste).
- **Segredos**: `MONGO_URI`, senha do painel e token do túnel só em `.env`/variáveis. Nunca em spec, log ou dashboard (o dashboard mostra `alias`, não hostname nem IP).
- **Auditoria/custos**: Cloudflare Zero Trust grátis até 50 usuários ([planos](https://www.cloudflare.com/plans/sase-zero-trust.md)). Tailscale Funnel tem banda não configurável ([Funnel](https://tailscale.com/kb/1223/funnel)). Nenhum custo previsto.
- **Térmico**: o vigia consertado vai pausar containers com frequência no laptop. Clipagem contínua + pausa = buracos (`lost`). O risco de desligamento cai, mas não some: o vigia só pausa containers, não processos do host.
- **Windows**: os scripts `sh` exigem uso via WSL. Sem temperatura de CPU. Suporte a GPU depende de driver com GPU-PV.

FIM
