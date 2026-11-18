# REV-onda-B: revisão independente da Onda B (fila Mongo, painel, capacidade, live contínua)

Verificador: Sonnet 5.5 (autor != verificador). Branch `onda-ab`, worktree `rev-b`. Spec lida: `onda-b-fila-mongo-dashboard/spec.md` (design e tasks consultados).

## Veredito: FAIL

A coordenação (`coord.py`), o painel, o vigia, a capacidade e o perfil estão corretos e bem testados. A live contínua (`follow_live`, `--follow`, `--ate-acabar`) **não usa o Mongo**: o critério "0 janelas processadas por dois PCs" não vale para o caso de uso principal da Onda B. Os testes passam porque só cobrem o caminho "claim negado".

## Evidência de teste

`docker run` com `clipes/`, `painel/`, `tests/`, `clipar`, `vigia-temp` e `entrypoint.sh` montados (o `docker compose` falhou ao criar a rede: "all predefined address pools have been fully subnetted"; mesma imagem, `--network none`, mongomock, nenhum Mongo real):
`pytest tests/test_coord.py test_capacidade.py test_perfil.py test_janelas.py test_monitor_coord.py test_monitor_live.py test_cli_ate_acabar.py test_painel.py test_vigia_temp.py test_clipar.py` → **97 passed**. Suíte global não rodada, por instrução.

## Achados

### Críticos / altos

1. **[bug, spec] `follow_live` ignora a coordenação: sem renovar lease, sem jobs no Mongo, e `--follow`/`--ate-acabar` nem recebem `coord`.**
   - `clipes/monitor.py:1609-1614` (`--follow`) e `clipes/cli.py:222` (`--ate-acabar`) chamam `follow_live` sem `coord`; `CLIPES_MONGO_URI` é ignorada. Dois PCs com `clipar <url> --ate-acabar` na mesma live gravam e clipam os dois: viola o Goal 1 e LIVE-02/LIVE-03.
   - Mesmo com `coord=` (só passado por testes): o laço `monitor.py:1142-1170` nunca chama `renew`/`beat`. Lease de 90 s vence, outro PC reivindica e marca os jobs do dono como `lost` (`coord.py:51-52`), e o dono continua gravando sem saber (viola AC 4/5).
   - `process_one` (`monitor.py:1132`) chama `run_worker_step` sem `coord`, e `close_window` (`:1099-1125`) não chama `add_job`: não existe job no Mongo (`_id live_id:fim`), então não há idempotência entre PCs, nem `lost` no backlog (`:1129`), nem `failed` (AC FILA-02).
   - Teste que falta: `test_monitor_coord.py:370-395, 500-520` só afirmam `not_claimed` / espera de claim; nenhum teste afirma lease renovado, job criado em `db.jobs`, ou `--follow`/`--ate-acabar` com URI definida.
   - Sugestão: `main(--follow)` e `run_pipeline(--ate-acabar)` montam `_CoordSession` quando houver URI; dentro de `follow_live` renovar lease a cada `CLIPES_BEAT_S` (parar o gravador e sair se `renew` falhar), `add_job` em `close_window`, `coord=` em `run_worker_step`, e `lost` no Mongo para o backlog. Testes: lease renovado em 3 ciclos, `db.jobs` com 1 doc por janela, 2 PCs `--follow` na mesma live → 1 grava.

2. **[bug] `release(live_id, True)` no `finally` marca a live `ended` mesmo em crash, Ctrl+C ou lease perdida** (`monitor.py:1181-1183`, `coord.py:61-65`). Após uma exceção (ex.: `RuntimeError` do gravador) a transmissão fica `ended` com a live ainda no ar e nenhum PC pode reivindicá-la (filtro exige `status="live"`). Sugestão: `ended=True` só quando o laço saiu pelo `break` de fim; senão `release(..., False)`. Teste: pool que levanta no meio → `lives.status == "live"` e `owner is None`.

### Médios

3. **[spec] AC 8 lido literal faz só 2 execuções reais.** `coord.py:80-94`: `start_job` incrementa `attempts` e, ao atingir 3, marca `failed` e devolve `False` antes de rodar; a 3ª partida nunca executa. O teste `test_coord.py:162-170` afirma exatamente isso, ou seja, espelha a implementação (o autor assumiu a leitura literal em comentário `ponytail:`). A spec diz "atingir `attempts >= 3` → failed" e o objetivo de "3 tentativas" é ambíguo. Recomendo decisão do usuário e, se a intenção for 3 execuções, marcar `failed` quando `attempts >= 3` **após** a falha da 3ª execução (ou o filtro de `start_job` aceitar `attempts < 3`). Hoje a leitura é defensável, mas deve ficar registrada em `spec.md` (assumption).

4. **[bug, médio] `cpu_pct` nunca é preenchido.** `coord.py:117` lê `capacity.get("cpu_pct")`, mas `capacity()` (`capacidade.py:147-154`) não devolve esse campo: o histórico do painel tem `cpu_pct: null` sempre, e o painel não mostra uso de CPU, só `cpus` (spec P2 e Goal 2 pedem "CPU, RAM, disco, temperatura, GPU"). Sugestão: ler `/proc/stat` (ou `os.getloadavg()/cpus`) em `capacity()` e testar o campo.

5. **[segurança, baixo-médio] Painel: CSRF e sem limite de tentativas.** `painel/api/_nucleo.py:61-83`: `POST /api/comando` aceita qualquer `Content-Type` e não confere `Origin`; o navegador reenvia o Basic Auth em post entre sites. Mitigação existente: precisa de `worker_id` válido e `confirmar=LIBERAR`. Sem rate limit na senha (a Vercel ajuda, mas o app não). Sugestão: exigir `Content-Type: application/json` (força preflight) e testar 415; senha longa documentada no README.

6. **[segurança/robustez, baixo] 409 não é atômico** (`_nucleo.py:54-56`: `find_one` e depois `insert_one`); duas requisições simultâneas inserem 2 `pending`. Sugestão: índice único parcial `{worker_id, status:"pending"}` ou `update_one(..., upsert=True)` com `$setOnInsert`. Nota: o usuário `painel` só tem insert/update em `commands`; `create_index` precisaria ser feito pelo `workers` (`Coord.ensure_indexes`).

7. **[bug, baixo] Painel abre um `MongoClient` por requisição e nunca fecha** (`_nucleo.py:71, 86-88`), vaza conexões em instâncias quentes da Vercel. Sugestão: cliente em variável de módulo (lazy) com `maxPoolSize=1`.

8. **[bug, baixo] vigia-temp: se o sensor some com o contêiner pausado, ele nunca retoma.** `vigia-temp:50-52`: no ramo `sem-sensor` não verifica `pausado=1`. O `trap` só despausa ao encerrar. Sugestão: despausar ao ler `sem-sensor` com `pausado=1` (ou registrar e despausar), com teste no `test_vigia_temp.py`.

9. **[bug, baixo] `capacity()` (nvidia-smi, timeout 5 s) é chamado de forma síncrona dentro de corrotinas** (`monitor.py:1344/1521`, via `_current_limits`), bloqueando o loop até 5 s por batida em PCs com driver lento. Sugestão: `asyncio.to_thread`.

### Testes

10. **[teste] Corrida só sequencial.** `test_coord.py` (`test_a_reivindica_b_recebe_false`, `lease_vencida`…) afirma resultados corretos, mas o "teste de corrida com 2 workers" do Success Criteria não existe: nenhum teste usa threads simultâneas. O mongomock não prova atomicidade, então o que vale é o desenho (um único `find_one_and_update` com filtro; confere em `coord.py:42-50`). Sugestão: teste com 2 threads e N lives garantindo 1 dono por live; documentar que atomicidade real é do Mongo.

11. **[teste] Sem teste do cenário de reatribuição ponta a ponta** (dono cai → B reivindica → B grava sem recriar job de `_id` existente) via `follow_live`/monitor; só `Coord.mark_lost` é testado unitariamente (`test_coord.py:177-206`).

### Positivos conferidos (com evidência)

- (1) Cada AC de `coord.py` tem teste com resultado esperado da spec (A/B, lease 91 s, renew 0 docs, concluir 2×, `lost`, 181 batidas → 180, ação fora da lista branca → `rejeitado`).
- (2) Claim: filtro único `status=live` + (`owner None` | `lease_until < agora` | `owner=self`), `DuplicateKeyError` tratado, `finish_job` com `{_id, owner, status:"processing"}` (`coord.py:96-101`). `_id = live_id:fim` dá o índice único por canal+transmissão+janela. Janela do dono perdido vira `lost` (`coord.py:103-107`) e `finish_job` nele falha, então não há `done` de job perdido.
- (4) Painel: `secrets.compare_digest` sobre bytes (`_nucleo.py:21`), 401 com `WWW-Authenticate`, 500 genérico sem a causa (`:65-66, 82-83`), `log_message` mudo, `index.html` só com `textContent` (`:165, 171, 265`; teste proíbe `innerHTML`), whitelist `ACOES` do lado do worker e `acao` fixa `liberar` no servidor, botão exige digitar `LIBERAR` e `confirm()` (`index.html:196-201`). URI: `mask_uri`/`_sanitize_error` e URI só do ambiente. `requirements.txt` e `vercel.json` sem segredo.
- (5) `vigia-temp`: lê `hwmon` de `k10temp|zenpower|coretemp|cpu_thermal`, cai para `thermal_zone`, grava `sem-sensor` e nunca `0C`; `clipar` só avisa (`clipar:63-65`) e reinicia após código 75 (`:73-76`).
- (6) Disco: `DiskQueue.complete` apaga `source.mkv` (`monitor.py:726`), `close_window` também nos rejeitados (`:1118`); janela `lost` passa por `complete`. Checagem de `CLIPES_MIN_FREE_GB` antes de abrir janela (`:1162`).
- `cpu_threads()` usado em ASR, LLM e embeddings (`asr.py:65,175`, `llm.py:231`, `topics.py:87`); `entrypoint.sh` exporta `OMP_NUM_THREADS` da cota, sobrescrevendo o 6 do `Dockerfile:15-16`. `max_lives` e `.perfil.json` conforme spec (`perfil.py:81-99`; `cpus` mudou → 1).

### (7) Testes pré-existentes afrouxados

Nenhum afrouxamento encontrado nos arquivos da onda. As edições em `tests/test_cli_llm.py` (`clip_01.txt` → `clip_01_<slug>.txt`) e `tests/test_render_eval.py` (nome de arquivo `..._part1of2_<slug>.mp4`) trocam o nome esperado por outro de mesma força, e vêm de outra onda (slug de título), fora do escopo desta revisão.

## Resumo

1 bloqueante (live contínua sem Mongo), 1 bug alto de estado (`ended` indevido), 1 decisão de spec (AC 8), 4 bugs/segurança baixos, 2 lacunas de teste. Corrigir os achados 1 e 2 e re-verificar antes de aprovar a Onda B.

FIM
