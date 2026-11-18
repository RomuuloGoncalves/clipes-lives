# CONSOLIDADO: pesquisa A/B/C/D (clipes-lives)

Data: 2026-11-17. Orquestrador nível 2 (Run run_b36a263a7f49). Base: 15 relatórios em `pesquisa/` + leitura de `clipes/*.py`, `docs/teste-real.md` e do `Dockerfile` do checkout principal.

Agentes: **claude** (sem sufixo: `A-plataformas.md`, `B-twitch.md`, `C-credito-modelos.md`, `D-deteccao-claude.md`), **sonnet** (`D-deteccao-sonnet.md`), **argon** (`*-argon*.md`, 6 relatórios), **cursor** (`*-cursor.md`, 4 relatórios).

Rótulos: **[M]** = medido por algum agente (diz onde); **[F]** = fonte com URL no relatório citado; **[E]** = estimativa; **NV** = não verificado. As medições de LLM, embeddings e ASR dos pesquisadores rodaram num **i5-10500 em container 6g/6cpu**, não no Ryzen 5 5500U. Só o ASR de `docs/teste-real.md` foi medido no piso.

---

## 1. Causa raiz do "assunto cortado" (todos concordam, conferido no código)

Três travas acopladas:
1. `candidates.py:15` `window_half=45`: o ASR só transcreve `pico ± 45 s`. Fora disso não há palavras.
2. `snap.py:16-17` `PEAK_POS_MIN/MAX = 0.35/0.75`, com o pré-roll ideal de 6–12 s (`snap.py:11-12`): o clipe começa no máximo ~41 s antes do pico.
3. `--max-dur 55` (padrão do `cli.py`).

Aumentar só o `max_dur` não resolve. A correção precisa de uma janela de ASR maior **e** de bordas pelo assunto, sem recentralizar no pico.
Hipótese do argon-A, **NV**: em conversa, o pico costuma cair perto do fim da história (punchline). Se for verdade, a janela deve ser **assimétrica**, com mais contexto antes do pico.

## 2. Decisões de produto

### 2.1 Duração e partes (feature `partes-por-assunto`)
| Assunto detectado | Saída |
|---|---|
| ≤ 65 s | clipe único, 25–65 s |
| 65–180 s | 2–3 partes de 45–58 s de conteúdo, "Parte k/N" |
| > 180 s | recorta para os 180 s em volta do pico (bordas de frase) e divide igual |

- **Teto de 58 s de conteúdo por parte.** Com o encerramento de +1,2 s (2.3), o arquivo fica ≤ 60 s. Fonte do motivo: Short com mais de 1 min e qualquer claim de Content ID fica bloqueado no mundo todo ([F] YouTube Help 15424877, citado por cursor-A e argon-A). Gravação de live tem música de fundo, então o risco é real.
- Corte entre partes **só** em fim de frase (`Word.ends_sentence`) ou pausa ≥ 0,5 s, a mesma regra do `snap.py`. Preferir a borda mais próxima do alvo `duração_total/N`.
- Gancho de fim de parte: corte na borda de frase + cartão "Parte k+1 →" (2.3). Teaser/flash-forward (argon-A) e recap de 1,5 s no início da parte seguinte ficam **fora da v1**: custam complexidade e não há medição de efeito.
- Fontes do 45–60 s: Shorts/Reels aceitam até 3 min [F]; a faixa 31–60 s rende mais watch time (OpusClip, dado próprio, sem método). Efeito de séries "Parte N": **NV**, todos os agentes concordam que não há dado.
- Canais BR reais publicam de 45 a 89 s, às vezes 156 s (argon-A, coleta de 2026-11-17 [F]). Isso apoia o clipe único de até 65 s.

### 2.2 Layout e legenda (feature `creditos-encerramento`, em `render.py`)
- **Manter o layout atual** (16:9 centralizado sobre fundo borrado) na v1. Webcam em cima + jogo embaixo, ou crop no rosto, é o padrão dominante nos exemplos (cursor-A, argon-A), mas o ganho é **NV** e o recorte depende de onde fica a webcam em cada canal. O YuNet já está na imagem (Dockerfile); reenquadrar fica como **tarefa opcional P2**, depois de crédito e encerramento.
- **Safe zone da legenda:** `MarginV 430 → 560` e `MarginL/R 70 → 120/200`, como constantes no `render.py`.
  - Os agentes divergiram: 520 (claude-A), 540 (argon-A), 560/630 (argon-C), 700 (cursor-A). Escolhi 560 porque põe a base da legenda em y=1360, logo abaixo do vídeo 16:9 (que vai até y=1264), sem cobrir a imagem.
  - Os 672 px de base dos templates de anúncio (Meta/Google [F]) jogariam a legenda para dentro do vídeo. Não existe safe zone oficial para post orgânico (todos concordam).
  - Validar olhando um quadro renderizado.
- Fonte, caixa alta, pop na palavra ativa: **fora da v1** (sem evidência medida).

### 2.3 Crédito e encerramento (feature `creditos-encerramento`)
- **Crédito na tela:** texto `twitch.tv/<canal>` (ou `@canal` no YouTube) em ASS, sobre o fundo borrado logo acima do vídeo (y≈600, x≥120). Forte nos primeiros ~4 s, depois fica a ~40% de opacidade até o fim. A receita testada em container é a do claude-C (`\pos`, `\fad`, `\alpha`). Tempo de tela é escolha de design, sem fonte.
- **Link completo** vai no texto do post, não no vídeo. Em Shorts, link na descrição não é clicável ([F] YouTube Help 13748639, fonte única: argon-A). Por isso o crédito precisa aparecer **dentro** do vídeo.
- **Encerramento** (nunca corte seco):
  - congelar o último quadro por 1,2 s (`tpad=stop_mode=clone:stop_duration=1.2`);
  - áudio com `loudnorm` primeiro e depois `afade` + `apad` (ordem do claude-C; o cursor pôs `apad` antes, o que normaliza silêncio, então descartei);
  - saída com `-t D+1.2`;
  - cartão ASS centrado no quadro congelado: "Parte k+1 →" nas partes 1..N-1, "Live completa: twitch.tv/<canal>" na última parte ou no clipe único.
  - **Sem fade para preto**, para não quebrar o loop.
  - Duração do cartão: 1,2 s (cursor), 1,2–1,5 s (argon) ou 2 s (claude). Escolhi 1,2 s por causa do teto de 60 s.
- Crédito **não** resolve direitos nem "conteúdo reutilizado" (claude-C, cursor-B, argon-B; ver Riscos).

### 2.4 Texto do post e hashtags (feature `legenda-hashtags`)
Formato fixo em `clips.json` (o mesmo para as 3 plataformas):
- `titulo`: ≤ 60 caracteres, pergunta ou exclamação, no máximo 1 emoji. Padrão real coletado por cursor-A (11 exemplos com views) e argon-A (12 exemplos).
- `legenda`, 3 linhas: (1) gancho com o nome do streamer/assunto; (2) `Crédito: <canal> · Live completa: <url>` e, se for série, `Parte k/N`; (3) hashtags.
- `hashtags`: **3 a 5**, específicas: canal/pessoa + jogo/tema + `#cortes`. **Proibido** `#fyp`, `#foryou`, `#viral`, `#reels`.
  - Teto de 5 no Instagram ([F] The Verge 2025-12-18 e outros) e no TikTok (desde ago/2025, [F] Search Engine Land, limite não anunciado oficialmente). Uma fonte fala em 3 no Instagram: conflito, então ficamos com ≤ 5.
  - Validar no código: começa com `#`, sem espaço nem hífen. Llama-3.2 gerou hashtag com hífen [M cursor].
- Não há fonte oficial que mostre ganho de `#fyp`. Há um contraexemplo observado (cursor-A).

## 3. Modelos (decisão do usuário já aplicada no Dockerfile)

| Papel | Padrão | Fallback / opção | Evidência |
|---|---|---|---|
| ASR | faster-whisper **small int8** | `large-v3-turbo` via `CLIPES_WHISPER_MODEL` | small: 0,49 xRT no piso com beam 5 e timestamps por palavra [M teste-real]; 0,29 xRT com beam 1 sem timestamps por palavra [M claude-C, i5] |
| LLM (título, legenda, hashtags, nota) | **Qwen3-1.7B Q4_K_M** (`--reasoning-budget 0` ou `/no_think`, ChatML, JSON schema) | **Qwen3-4B-Instruct-2507 Q4_K_M** (`WITH_LLM_4B=1`) | 1.7B: 16–22 tok/s, ~2 GB RSS, ~8–11 s/clipe [M argon-C e cursor-C]. 4B: 7,3–8,9 tok/s, 4,4–4,5 GB RSS, 15–24 s/clipe [M idem] |
| Segmentar assunto | **MiniLM-L12 multilíngue ONNX int8** + pausa ≥ 0,8 s | só pausas/fim de frase se o ONNX falhar | contraste 0,52 vs 0,02 (cursor), Δ 0,137 vs 0,006 (argon) [M]; TextTiling léxico **falhou** (0/2 fronteiras) [M argon, trecho sintético de 3 min] |
| Rosto | YuNet (na imagem) | layout atual | 7–14 ms/quadro a 640x360 [M argon, cursor]; só P2 |
| Risada/grito | **nenhum modelo** (loudness + `_extend_past_reaction` já cobrem) | — | consenso dos 3 relatórios C |
| Descartados | distil-large-v3 (só inglês [M claude-C]), Qwen2.5-3B (licença research), Gemma e Llama (gated), YAMNet/PANNs | | |

Regras:
- **Whisper e LLM nunca juntos na RAM.** O LLM roda como subprocesso `llama-server` (binário em `/opt/llama`), sobe uma vez por lote depois de todo o ASR e morre no fim. Não usar `llama-cpp-python`: falhou ao compilar [M claude-C].
- **Plugável sem abstração.** O caminho do GGUF vem de `CLIPES_LLM_GGUF` (já no Dockerfile). Uma função `complete(prompt, schema) -> dict`. Sem classe nem registry.
- **Nota 0–10:** os 5 LLMs deram 7–9 para o mesmo trecho [M cursor-C]. No Rhapsody, LLM zero-shot quase empata com o acaso ([F] arXiv 2505.19429, citado por claude-D e sonnet-D). Então:
  - `score` final continua vindo dos sinais (o `raw_score` de hoje);
  - a nota do LLM sai de uma rubrica com âncoras, temperatura 0, e fica só como **feature/desempate** e para gravar no manifesto;
  - **não** descartar clipe por nota do LLM na v1 (argon-C sugeria `nota < 6`: rejeitado).
- **Anti-alucinação barata:** o título não pode trazer nome próprio (palavra capitalizada) que não esteja na transcrição. Os 1.5B/1.7B inventaram fatos [M cursor-C]. Se falhar, usa o título heurístico atual (`generate_title`).

### 3.1 Orçamento no piso (VOD 1 h, 8 clipes, sequencial)
| Etapa | Tempo | RAM pico | Base |
|---|---:|---:|---|
| ingest + sinais | ~25 s | < 0,5 GB | [M teste-real] |
| ASR small (janelas de assunto, ver 3.2) | ~8–12 min | ~1,6 GB | [E] sobre 0,49 xRT medido |
| segmentar (MiniLM) | < 5 s | +0,2–0,8 GB | [M i5] |
| LLM 1.7B, 8 clipes | ~1,5–2 min (+10–20 s de carga a frio) | ~2 GB | [M i5]; 5500U **NV** |
| render (com cartão de 1,2 s) | ~8 min | < 1 GB | [M teste-real] |
| **Total** | **~18–23 min por hora de VOD** | **≤ 2 GB** (4,5 GB com o 4B) | |

### 3.2 Janela de ASR para assuntos de até 3 min (impacto direto no orçamento)
Hoje: 16 candidatos × 90 s ≈ 24 min de áudio = 703 s.
Proposta: janela assimétrica `[pico−150 s, pico+60 s]` (210 s) **só nos top `max_clips` candidatos** (8, não 16). Dá 8 × 210 s = 28 min de áudio antes do merge.
- Com beam 5: ~13,7 min de ASR.
- Com `beam_size=1` + timestamps por palavra: **NV**, provavelmente ~0,3–0,4 xRT, ou seja, ~9–11 min.
- **Medir antes de fixar** (M1 e M2 em §7). `beam_size=1` muda a qualidade do texto: medir junto.

## 4. Monitor Twitch (feature `monitor-twitch`, módulo novo)

```
discover  -> lista de canais: channels.txt (padrão, sem credencial) OU Helix Get Streams
             se TWITCH_CLIENT_ID/SECRET estiverem no env (opcional)
chat      -> 1 processo asyncio, IRC anônimo justinfan, <= 80 canais, JOIN a cada 0,6 s
             -> contagem 1 Hz por canal -> evento de pico
record    -> ate 3 canais "quentes" + 1 sob demanda:
             streamlink --stdout <canal> 720p,480p,best | ffmpeg -c copy -f segment -segment_time 10
             buffer rolante de 5 min por canal (limpeza > 300 s)
queue     -> OUT_DIR/queue/{pending,processing,done,rejected}/<job>/ (JSON + rename atômico)
worker    -> 1 job por vez: source.mkv + chat.json -> pipeline atual (sinais->ASR->partes->LLM->render)
```

- **Descoberta:** Helix exige app registrado (client_id/secret) [F dev.twitch.tv]. Isso choca com "sem chave de API". **Padrão = `channels.txt`**; Helix só se o usuário puser as credenciais no env. Credencial nunca em arquivo. Listar categoria por GQL interno quebra com mudança de hash e anti-bot (argon-B): **não usar**.
- **Pico relativo** (une argon-B, cursor-B e claude-B; reaproveita a fórmula Poisson de `signals.py:218`), janela de 10 s:
  - `expected = max(mediana_10s dos últimos 180 s excluindo os 15 s mais recentes, 0,4·média, 2,5)`
  - `z = (count10 − expected)/sqrt(expected+1)`
  - dispara se `count10 ≥ 6` **e** `autores_unicos10 ≥ 4` **e** `z ≥ 3,5` **e** `count10/expected ≥ 3`
  - aquecimento de 60 s ao entrar no canal; cooldown de 180 s por canal
  - ignorar bots (`BOT_AUTHORS` do `ingest.py`), mensagens que começam com `!` e duplicatas de Shared Chat (`source-id` [F IRC doc, cursor-B])
  - Os limiares são escolha de design, **NV**: calibrar com logs reais.
- **Pré/pós-roll:** pré 180 s (para caber um assunto de até ~3 min, ver §1) e pós até o chat voltar ao baseline, com mínimo de 60 s e máximo de 120 s. Os agentes divergiram: buffer de 90 s (argon), 6 min (claude), 10 min (cursor). Com 5 min, o pré de 180 s cabe.
- **Gravação:** streamlink, porque filtra anúncios (o anúncio vira pausa/descontinuidade) [F streamlink docs]; `-c copy`, sem reencode. É dependência nova (pip, **só no Dockerfile**). Concat dos segmentos com `-c copy` só depois do pós-roll.
- **Capacidade** [E, NV]: 3+1 gravações a 720p ≈ 12–18 Mbit/s, ~150 MB de disco por canal com 5 min de buffer, CPU de poucos %. O que limita é o worker (ASR/render). Medir: M5.
- **Chat do job:** salvar as mensagens do IRC da janela gravada em `chat.json` no formato `[t_rel, texto, autor_idx]`, que é o que `signal_chat` espera. Assim o worker reaproveita os sinais.
- **Entrada do pipeline:** `cli.py`/`ingest.py` passam a aceitar **arquivo local** (`source.mkv` + `meta.json` com canal e URL), sem `yt-dlp`. O render já corta de `live_video_path`.
- **Fila e "1 post a cada 45 min":** nesta rodada não se publica (decisão 2). O worker grava `ready_at` e ordena por `score`. Publicação fica para depois.
- **Política por canal** (`policy.json`): `blocked`, `allowed` opcional e `delay_h`. Exemplo real: o Gaules só autoriza cortes 24 h depois do fim da VOD ([F] descrição de `7pfvbXVmrtE`, cursor-B). Job de canal bloqueado não grava.
- **Create Clip:** **não usar** como fonte de vídeo (máximo de 60 s, exige user token `clips:edit`, conflita com a decisão 5) [F]. Fora da v1.

## 5. Frente D (detecção sem picos): fora das 4 features desta rodada

Concordâncias entre sonnet, claude, argon-A/B/C e cursor:
- o padrão de mercado é segmentar tudo, pontuar cada segmento e ranquear;
- limiar por **percentil do vídeo**;
- **cota por hora** com piso de qualidade;
- dedup temporal + textual;
- diversificação ao longo do vídeo;
- LLM nunca como juiz principal;
- calibrar com precision@K num conjunto de 20–50 rótulos, usando os clipes de `docs/teste-real.md` como positivos fracos (viés circular).

Divergências:
| Ponto | Posições |
|---|---|
| ASR da triagem | small beam 1 (claude-D, argon-C B), tiny/base (argon-A/B/C) — o cursor recusa tiny (WER ~24% em pt [F Handy]) |
| Orçamento | 25% da duração (cursor), 30–50% (claude), 35–40% (argon) |
| Custo por hora de vídeo | 2,6–4,2 min/h (argon-A, com RTF de tiny/base **estimado**) vs 6–12,5 min/h (claude, argon-B/C, com base no 0,29–0,49 **medido**). Fico com **6–8 min/h** até medir |
| Separação mínima | 120 s (sonnet) vs 300 s (claude) vs 2 por bloco de 10 min (cursor) |
| Cota | 3/h (claude) vs 4/h (sonnet, cursor) |
| Atalho YouTube | legenda automática `json3` (argon-B) com custo de CPU ~0. **NV** que tenha timestamp por palavra e que exista em lives longas pt-BR |

Recomendação: nova feature **`deteccao-por-cota`** depois desta rodada, a decidir pelo usuário. O 1º passo é pequeno e sem ASR novo: percentil + completar a cota em `candidates.py`.
Na rodada atual, `partes-por-assunto` só precisa receber o candidato e estender as bordas. Isso não conflita com a D.

## 6. Riscos

1. **Direitos e ToS da Twitch.**
   - A ToS proíbe "downloading … except as expressly permitted" e acesso automatizado ([F] legal.twitch.com, lido pelo cursor-B; claude-B não conseguiu carregar).
   - Gravar com streamlink não é API.
   - Repostar fora do botão Share oficial não tem a licença do §8.a.
   - Música ao vivo vai inteira para o clipe (o VOD é silenciado, a live não) → claim/strike (argon-B).
   - Mitigação: só detectar/gravar/enfileirar (decisão 2), `policy.json`, crédito, retenção curta do buffer.
   - **Decisão jurídica é do usuário. Não é aconselhamento.**
2. **"Conteúdo reutilizado" no YouTube:** crédito não protege o alcance nem a monetização ([F] secundárias, claude-C; política oficial NV).
3. **IRC `justinfan` não é documentado** [F fórum dev Twitch]. Pode quebrar ou ser limitado sem aviso. O EventSub não lê sem login.
4. **`ingest.py` já usa GQL interno** (`gql.twitch.tv` + Client-Id do player web) para o chat de VOD: mesma fragilidade e risco de ToS (argon-B). Já existia antes; registrar.
5. **`mem_limit: 8g` no `docker-compose.yml` do checkout principal** (mudança local ainda não commitada; a worktree tem `6g`). O host tem 9 GB, sobra ~1 GB para o SO. A restrição dura combinada era 6 GB. Só se justifica com o 4B; com o 1.7B, 6g basta (pico ≤ 2 GB). **Confirmar com o usuário.**
6. **Medições fora do piso:** LLM, embeddings e YuNet foram medidos no i5-10500. O 5500U deve ser 10–40% mais lento [E cursor].
7. **Sobra no host:** o pesquisador cursor deixou **8,8 GB em `~/.cache/pesq-bench/`** (GGUFs Qwen3-1.7B, Qwen3-4B, Llama-3.2-3B, llama.cpp b12470, quadros, scripts). Isso viola a regra "nada no host". **Não apaguei** (não é meu); o usuário decide.
8. **Afirmações de fonte única ou fraca:** links de Shorts não clicáveis (argon-A); números de safe zone e "Creator Rewards > 60 s" (blogs); InfluenceFlow citado só pela raiz do site (argon-A). Tratar como NV fraco.

## 7. Medições pendentes (todas dentro do container do projeto, no 5500U)

- **M1:** WER pt-BR **small vs large-v3-turbo** num trecho de 5 min de `UBv07VGeD24` com referência corrigida à mão.
- **M2:** tempo e qualidade do `small` com `beam_size=1` + `word_timestamps=True` vs o atual (beam 5), nas mesmas 16 janelas do teste real.
- **M3:** Qwen3-1.7B e 4B no 5500U: tok/s, RSS e s/clipe (`llama-bench -t 6` + 8 clipes reais), e conferir título/hashtags à mão.
- **M4:** MiniLM em transcrição **real** de live (não sintética): acerto de fronteira em 3–5 assuntos marcados.
- **M5:** 3 gravações streamlink `-c copy` simultâneas + worker rodando: CPU, RAM, disco e banda.
- **M6:** IRC justinfan com 50–80 canais por 1 h: estabilidade e falsos picos com os limiares de §4.
- **M7:** ASR em duas passadas (frente D), só se `deteccao-por-cota` for aprovada.

## 8. Orientação para o planejador (spec/design/tasks)

- Features e posse de arquivos:
  - `partes-por-assunto`: `candidates.py`, `asr.py`, `snap.py`, novo `topics.py`, e trechos de `cli.py`/`render.py` (rótulo da parte).
  - `creditos-encerramento`: `render.py` + `cli.py`.
  - `legenda-hashtags`: novo `llm.py` + campo no manifesto.
  - `monitor-twitch`: novo `monitor.py` (+ `ingest.py`: entrada de arquivo local).
- **`render.py` e `cli.py` em série:** primeiro partes-por-assunto, depois creditos-encerramento. `monitor-twitch` e `legenda-hashtags` podem ir em paralelo (módulos novos). O único ponto de contato deles com `cli.py` fica numa tarefa final de integração, depois das duas em série.
- Cada tarefa: `[repo: clipes-lives]`, dependências explícitas, arquivos dono, e critério observável (teste `pytest` no container ou quadro/`ffprobe` conferido). Exemplos: "nenhuma parte > 58 s de conteúdo", "arquivo final = D+1,2 s ±0,05", "hashtags 3–5 sem `#fyp`".
- Sem abstração: sem classe de provider de LLM, sem framework de fila, sem banco. JSON + rename.
