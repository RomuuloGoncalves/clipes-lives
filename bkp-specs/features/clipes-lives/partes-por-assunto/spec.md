# partes-por-assunto Specification

## Problem Statement

Os clipes gerados hoje saem incompletos e cortam histórias/assuntos no meio por três travas acopladas no código: `candidates.py` limita a janela de ASR a `pico ± 45s`, `snap.py` força o pico acústico a cair entre `35%` e `75%` da duração com pré-roll máximo de ~41s, e `cli.py` trava `--max-dur 55`. Precisamos ampliar a janela de transcrição ao redor dos candidatos, detectar início e fim reais do assunto (até ~3 minutos) com MiniLM ONNX int8 + pausas, e emitir clipe único (`25–65s`) ou dividir assuntos longos em 2–3 partes sequenciais de `45–58s` de conteúdo (`Parte k/N`) sem cortar palavras ou frases.

## Goals

- [ ] Eliminar cortes no meio do assunto ampliando a janela de ASR para `[pico - 150s, pico + 60s]` nos top `max_clips` candidatos e ancorando o corte nas fronteiras do tópico (`sentence_edge_rate >= 0.90`, `mid_word_cut_rate == 0.0`).
- [ ] Emitir clipe único de `25–65s` quando o assunto tiver `<= 65s`, ou dividir assuntos de `65–180s` em `2–3` partes consecutivas de `45–58s` de conteúdo (`Parte 1/N .. Parte N/N`) com arquivo final `<= 60.0s` após o encerramento de `+1.2s`.
- [ ] Medir em container no Ryzen 5 5500U o WER pt-BR (`small` vs `large-v3-turbo`, M1) e o acerto de fronteiras do MiniLM ONNX em transcrição real de live (M4).

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
| ------- | ------ |
| Teaser / flash-forward no segundo 0 | Rejeitado no CONSOLIDADO §2.1 por complexidade de edição e ausência de evidência medida de retenção |
| Recap de 1,5s no início da Parte 2..N | Fora da v1 (CONSOLIDADO §2.1); partes conectam por borda de frase e rótulo `Parte k/N` |
| Diarização neural de falantes (pyannote) | Exige token gated no Hugging Face e estoura o orçamento de CPU/RAM do piso |
| Publicação automática das partes em redes sociais | Fora do escopo desta rodada (decisão dura do usuário) |

---

## Implicit-Requirement Dimensions Sweep

| Dimension | Resolution |
| --------- | ---------- |
| Input validation & bounds | Assunto limitado a `[25.0s, 180.0s]`; assuntos `> 180.0s` são recortados para `180.0s` ao redor do pico em bordas de frase; cada parte tem conteúdo estritamente em `[45.0s, 58.0s]` (ou `[25.0s, 65.0s]` para clipe único) |
| Failure / partial-failure states | Se o modelo ONNX MiniLM (`/opt/models/embed/onnx/model_quint8_avx2.onnx`) falhar ao carregar ou inferir, o sistema degrada para segmentação léxica/pausas (`gap >= 0.8s` + `Word.ends_sentence`) sem interromper o pipeline |
| Idempotency / retry / duplicate handling | `words.json` continua cacheado por tuplas `(round(s, 2), round(e, 2))` e nome do modelo; partes do mesmo assunto recebem `group_id` comum e não são eliminadas entre si pelo NMS temporal |
| Auth boundaries & rate limits | N/A because todos os modelos (`faster-whisper` e `MiniLM ONNX`) já estão assados localmente em `/opt/models` sem chamadas de rede na segmentação |
| Concurrency / ordering | Execução estritamente sequencial em CPU (`CLIPES_CPU_THREADS=6`); partes de um mesmo assunto são ordenadas por `part_index` (`1..N`) em `clips.json` |
| Data lifecycle / expiry | Cache intermediário permanece em `<out_dir>/.cache/` e imagens/volumes são integralmente removíveis via `docker compose down --rmi all -v` |
| Observability | `clips.json` e `README.md` registram `part_index`, `part_total`, `group_id`, `topic_duration` e `timings["topics_s"]` |
| External-dependency failure | Na ausência de `onnxruntime` ou arquivo `.onnx`, fallback imediato para fronteiras de pausa/pontuação |
| State-transition integrity | Nenhuma parte é emitida com `part_total > 1` se alguma parte irmã violar `mid_word_cut_rate == 0.0` ou ficar abaixo de `min_dur` (`25.0s`); nesse caso emite apenas o melhor corte único válido |

---

## Assumptions & Open Questions

Every ambiguity is resolved or recorded here - nothing is left silently unclear.

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --------------------- | -------------- | --------- | ---------- |
| Assimetria da janela de ASR | `[pico - 150.0s, pico + 60.0s]` (210s) nos top `max_clips` candidatos | Em conversas e histórias, o pico de risada/reação ocorre perto da punchline final; 150s antes captura o setup completo mantendo o áudio total em ~28 min para 8 candidatos (CONSOLIDADO §1 e §3.2) | yes |
| Teto de duração por parte em multi-parte | `58.0s` de conteúdo (`<= 59.2s` com `+1.2s` de cartão final) | Shorts `> 60.0s` com qualquer claim de Content ID (música de fundo em live) são bloqueados globalmente no YouTube (YouTube Help 15424877, CONSOLIDADO §2.1) | yes |
| Limiar de vale semântico no MiniLM ONNX | Similaridade de cosseno (janela de 3 frases à esquerda vs 3 à direita) `< media - 1.0 * desvio` alinhada a fim de frase ou pausa `>= 0.8s` | Medido nos relatórios C (contraste 0,52 vs 0,02 no cursor-C; delta 0,137 no argon-C) e calibrável na task M4 | yes |
| Gancho entre partes | Corte em fim de frase + selo `Parte k/N` no topo + cartão final `"Parte k+1 →"` nos últimos `1.2s` | Decisão explícita do CONSOLIDADO §2.1 e §2.3 (estilo ponytail: sem flash-forward ou recap) | yes |

**Open questions:** none - todas as decisões foram resolvidas no CONSOLIDADO ou registradas como premissas explícitas acima.

---

## User Stories

### P1: Janela de ASR Assimétrica e Segmentação de Assunto com MiniLM ONNX ⭐ MVP

**User Story**: Como criador de cortes, quero que o pipeline transcreva contexto suficiente antes e depois do pico e identifique onde o assunto começa e termina para que o clipe não corte a história no meio.

**Why P1**: Resolve a causa raiz do "assunto cortado" identificada em `candidates.py`, `asr.py` e `snap.py`.

**Acceptance Criteria**:

1. WHEN `pick_candidate_peaks` seleciona os candidatos para transcrição THEN o sistema SHALL definir `start_win = max(0.0, peak - 150.0)` e `end_win = min(duration, peak + 60.0)` para os top `max_clips` candidatos.
2. WHEN a lista de `Word` de uma janela transcrita é entregue ao segmentador `segment_topics` THEN o sistema SHALL agrupar as palavras em frases (`Word.ends_sentence` ou pausa `>= 0.8s`), calcular embeddings via `CLIPES_EMBED_ONNX` (`/opt/models/embed/onnx/model_quint8_avx2.onnx`) e delimitar o intervalo `[topic_start, topic_end]` nas fronteiras onde a similaridade de cosseno entre blocos de 3 frases cair abaixo de `media - 1.0 * std` coincidindo com fim de frase ou pausa `>= 0.8s`.
3. IF o arquivo ONNX em `CLIPES_EMBED_ONNX` estiver ausente ou falhar na inferência THEN o sistema SHALL registrar aviso em log e delimitar `[topic_start, topic_end]` usando exclusivamente fronteiras de fim de frase seguidas de pausa `>= 0.8s`.
4. WHEN um assunto válido de duração `<= 65.0s` e `>= min_dur` (`25.0s`) é delimitado THEN o sistema SHALL ajustar `start` e `end` do `SnappedClip` às bordas do assunto sem descartar candidatos cujo `peak_ratio` caia fora de `[0.35, 0.75]`.

**Independent Test**: Rodar `docker compose run --rm clipes pytest tests/test_topics.py tests/test_snap.py` verificando que uma história cujo pico está em 85% do arco (`peak_ratio = 0.85`) gera um clipe único completo de `25–65s` sem cortar o setup inicial.

---

### P1: Divisão de Assuntos Longos (65–180s) em Partes 1..N com Selo e Gancho ⭐ MVP

**User Story**: Como criador de cortes, quero que assuntos entre 65s e 180s sejam divididos automaticamente em 2 ou 3 partes de 45–58s cortadas em fim de frase e identificadas como `Parte k/N` para publicar histórias completas sem estourar o limite de 60s dos Shorts.

**Why P1**: Atende diretamente à decisão de produto §2.1 do CONSOLIDADO para assuntos de até ~3 minutos.

**Acceptance Criteria**:

1. WHEN o assunto detectado tiver duração em `(65.0s, 180.0s]` THEN o sistema SHALL dividi-lo em `N` partes consecutivas (`N = 2` se duração `<= 116.0s`, `N = 3` se `> 116.0s`) onde cada parte possui duração de conteúdo `<= 58.0s` (e preferencialmente `>= 45.0s`, nunca `< min_dur`) e corta exclusivamente em `Word.ends_sentence` ou pausa `>= 0.5s` mais próxima de `duracao_total / N`.
2. WHEN o assunto detectado exceder `180.0s` THEN o sistema SHALL recortar o intervalo para no máximo `180.0s` ao redor do pico respeitando bordas de frase antes de dividi-lo em partes de até `58.0s`.
3. WHILE um clipe pertencer a uma sequência multi-parte (`part_total > 1`) o sistema SHALL manter todas as partes do mesmo `group_id` no NMS de `select_clips`, nomear os arquivos como `clip_XX_part1ofN.mp4` (ou incluir `part_index` e `part_total` em `clips.json`) e queimar o selo `Parte k/N` no arquivo `.ass` dentro da safe zone superior (`y = 490`).
4. The sistema SHALL garantir `mid_word_cut_rate == 0.0` em todas as bordas internas e externas das partes geradas.

**Independent Test**: Executar `pytest` com uma transcrição contínua de `135s` e verificar que saem exatas 3 partes (`Parte 1/3`, `Parte 2/3`, `Parte 3/3`), todas com duração `<= 58.0s`, `mid_word_cut_rate == 0.0` e estilo `PartBadge` presente no `.ass`.

---

### P2: Medições no Piso Ryzen 5 5500U (M1 WER pt-BR e M4 Fronteiras Reais)

**User Story**: Como engenheiro mantendo o pipeline no piso Ryzen 5 5500U, quero testes de benchmark reprodutíveis em container para medir o WER em pt-BR (`small` vs `large-v3-turbo`) e a precisão das fronteiras do MiniLM ONNX em transcrição real.

**Why P2**: Fecha as medições pendentes M1 e M4 do CONSOLIDADO §7 diretamente no hardware-piso.

**Acceptance Criteria**:

1. WHEN a suíte de medição M1 (`tests/test_bench_m1_wer.py`) é executada no container sobre o trecho de referência pt-BR THEN o sistema SHALL calcular e registrar o WER exato, o `xRT` e o pico de RSS de `small` (`xRT <= 0.60`, `RSS <= 2.0 GB`) e `large-v3-turbo` (`RSS <= 3.5 GB`).
2. WHEN a suíte de medição M4 (`tests/test_bench_m4_topics.py`) é executada sobre transcrição real com fronteiras de assunto anotadas THEN o sistema SHALL verificar tempo de segmentação `< 5.0s`, contraste médio de cosseno intra vs inter-assunto `>= 0.10` e acerto (recall com tolerância de `±1` frase ou `±3.0s`) `>= 0.75`.

**Independent Test**: Rodar `docker compose run --rm clipes pytest tests/test_bench_m1_wer.py tests/test_bench_m4_topics.py -v -s`.

---

## Edge Cases

- IF uma janela candidata tiver menos de 4 palavras transcritas (ex.: clipe puramente musical ou grito isolado) THEN o sistema SHALL contornar `segment_topics` e aplicar o `_silence_snap` de `snap.py` gerando clipe único.
- IF a divisão de um assunto de `65–180s` não encontrar nenhuma borda de frase ou pausa `>= 0.5s` que mantenha todas as partes entre `min_dur` (`25.0s`) e `58.0s` THEN o sistema SHALL emitir apenas o clipe único de maior qualidade (`25.0s–65.0s`) contendo o pico.
- WHEN `max_clips` for atingido durante a seleção de um assunto multi-parte THEN o sistema SHALL incluir todas as partes `1..N` do último assunto aceito ou descartar o assunto inteiro, nunca emitindo uma série truncada sem o desfecho (`Parte 1/2` sem `Parte 2/2`).

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| -------------- | ----- | ----- | ------ |
| PART-01 | P1: Janela de ASR Assimétrica `[pico-150s, pico+60s]` | Design | Pending |
| PART-02 | P1: Segmentação de Assunto com MiniLM ONNX e Fallback | Design | Pending |
| PART-03 | P1: Clipe Único `25–65s` Ancorado no Assunto | Design | Pending |
| PART-04 | P1: Divisão `65–180s` em 2–3 Partes de `45–58s` | Design | Pending |
| PART-05 | P1: Selo `Parte k/N` no ASS e Metadados em `clips.json` | Design | Pending |
| PART-06 | P2: Medição M1 (WER pt-BR `small` vs `large-v3-turbo` no 5500U) | Design | Pending |
| PART-07 | P2: Medição M4 (Fronteiras MiniLM ONNX em Transcrição Real) | Design | Pending |

**Coverage:** 7 total, 7 mapped to tasks, 0 unmapped

---

## Success Criteria

- [ ] Clipes únicos têm entre `25.0s` e `65.0s` de conteúdo e partes de séries têm entre `25.0s` (alvo `45.0s`) e `58.0s` de conteúdo (`100%` dos arquivos finais `<= 60.0s` após o encerramento de `+1.2s`).
- [ ] `mid_word_cut_rate == 0.0` e `sentence_edge_rate >= 0.90` em todos os clipes e partes.
- [ ] Medições M1 e M4 executadas dentro do container no Ryzen 5 5500U com resultados registrados.
