# Pesquisa D (Extra): Detecção de Bons Momentos SEM Depender de Picos (nem de Heatmap)

- **Agente / Worktree:** `pesq-argon-C` (Gemini 4 Argon High)
- **Data da pesquisa:** 2026-11-17
- **Escopo:** Resolver o gargalo estrutural de `clipes/signals.py`, `clipes/candidates.py` e `clipes/snap.py` quando o vídeo não tem gritos/explosões (podcasts conversados, lives calmas, análises, histórias longas) nem metadados externos (sem replay de chat e sem heatmap `"Most Replayed"` do YouTube).

---

## Diagnóstico do Código Atual: Por que o Pipeline Falha Sem Picos Fortes?

A leitura linha a linha de `clipes/signals.py`, `clipes/candidates.py` e `clipes/snap.py` revela **três bloqueios matemáticos** que impedem bons clipes em lives/podcasts conversados:

1. **Zona morta dura de decibéis em `signals.py::signal_loudness` (linhas 130 e 138):**
   - `excess = np.maximum(0.0, db - baseline - 3.0)` e `jump = np.maximum(0.0, db - prev_mean - 4.0)`.
   - Em podcasts de estúdio (*Podpah*, *Flow*, *Inteligência Ltda.*) ou streamers com filtro compressor/limiter no OBS, a voz oscila dentro de `±2.0 a 2.5 dB` da mediana de 60s. Resultado: `excess = 0` e `jump = 0` durante **minutos inteiros de histórias excelentes**.
   - Além disso, `robust_percentile_norm(..., min_scale=8.0)` (linha 143) divide o resíduo por no mínimo `8.0 dB`, esmagando variações prosódicas normais para perto de zero.
2. **Acoplamento estrito ao pico em `snap.py` (linhas 16–17, 156–157, 312 e 334):**
   - `PEAK_POS_MIN = 0.35` e `PEAK_POS_MAX = 0.75` obrigam o pico acústico a cair exatamente entre **35% e 75%** da duração do clipe (`25s–55s`).
   - **Por que isso corta o assunto no meio (Decisão 5):**
     - Em uma história ou piada, a risada/clímax quase sempre ocorre nos **últimos 10–15% do arco narrativo (`ratio = 0.85–0.92`)**, logo antes de mudar de assunto. Ao exigir `ratio <= 0.75`, `snap.py` é forçado a incluir `10s–18s` do **assunto seguinte** após a risada e a cortar o início (*setup*) da história!
     - Em um debate ou opinião forte, o gancho explosivo ocorre no **início (`ratio = 0.10–0.25`)** seguido de 40s de explicação. O filtro `35%–75%` descarta ou deforma esse arco.
3. **Dependência de um único ponto temporal (`CandidatePeak.peak`):**
   - Uma história de 45s ou 2m30s não é um "instante $t$ com janela simétrica $\pm 45\text{s}$" (`candidates.py:57-58`); é um **intervalo semântico $[t_{\text{setup}}, t_{\text{payoff}}]$**.

---

## 1. Como Ferramentas e Literatura Detectam Highlights Sem Picos de Áudio

| Ferramenta / Paper | Como Escolhe Momentos Sem Pico de Áudio | O que é Reutilizável em CPU (`6 GB RAM / 6 Threads`) | Fonte e Data Verificada |
|---|---|---|---|
| **RHAPSODY (Park et al., COLM 2025)** | Dataset de 13.000 episódios de podcast pareados com a curva real *Most Replayed* do YouTube. Demonstra que em podcasts conversados a amplitude de áudio tem baixa correlação com replay; treina/avalia modelos leves (**`Llama-3.2-1B/3B`** e classificadores sobre segmentos de transcrição) combinando estrutura retórica do texto com prosódia de fala. | **100% reutilizável:** dividir a transcrição em candidatos de arco e pontuar via LLM leve (1.5B–1.7B) + features prosódicas/estruturais, em vez de usar volume bruto. | Paper: https://arxiv.org/abs/2505.19429 e Código: https://github.com/salty-flower/RHAPSODY (verificado em 2026-11-17) |
| **Identifying Narrative Content in Podcast Transcripts (Abdessamed et al., EACL 2024)** | Extrai arcos narrativos (*staging -> plot progression -> cognitive tension*) dentro de conversas não-lineares de podcast usando mudanças de tempo verbal, marcadores de história pessoal (`"eu estava"`, `"aí ele falou"`) e tensão cognitiva. | **100% reutilizável (custo ~0):** léxico de marcadores narrativos pt-BR (setup, conflito, desfecho) + pontuação de completude narrativa antes do LLM. | Paper ACL Anthology: https://aclanthology.org/2024.eacl-long.102/ (verificado em 2026-11-17) |
| **ClipsAI (Open Source)** | Transcreve o áudio com timestamps por palavra e aplica **TextTiling com embeddings densos** sobre blocos de frases em múltiplas escalas para encontrar blocos temáticos autossuficientes de 30s–180s sem olhar para picos de volume. | **Reutilizável com adaptação:** usar janelas de frases + cosseno de embeddings ONNX (`paraphrase-multilingual-MiniLM-L12-v2` INT8) ou fronteiras de pausa/marcadores discursivos. | GitHub: https://github.com/ClipsAI/clipsai e Docs: https://docs.clipsai.com/ (verificado em 2026-11-17) |
| **OpusClip (`ClipAnything`) & Vizard** | Pipeline *Transcript-First*: (1) agrupa transcrição em capítulos/tópicos; (2) busca pares **Hook (pergunta/afirmação forte) + Story/Body + Payoff (conclusão/risada)**; (3) atribui um *Virality Score (0–100)* baseado na força da 1ª frase e na ausência de frases inacabadas. | **Reutilizável:** heurística de *Hook Strength* na 1ª frase + verificação de fechamento semântico + nota 0–10 do LLM local. | OpusClip: https://www.opus.pro/clip-anything e Vizard: https://vizard.ai/ (verificado em 2026-11-17) |

---

## 2. Abordagem "Segmentar Tudo + Pontuar Tudo" (Unidades de Assunto e Arco Narrativo)

Em vez de centrar janelas cegas de 55s num pico de dB, a seleção passa a operar sobre **Unidades de Assunto (`TopicUnit`)**:

### Passo 2.1 — Construção das Unidades de Assunto (Arco Completo de `25s` a `180s`)
A partir da lista de palavras/frases transcritas:
1. **Agrupamento em Cláusulas/Frases (`SentenceSpan`):** junta palavras até pontuação final (`.?!`) ou pausa acústica `>= 0.65s`.
2. **Detecção de Fronteiras de Tópico (Início e Fim de Assunto):** uma fronteira de assunto ocorre quando há combinação de:
   - **Pausa longa de transição (`gap >= 0.9s`)**;
   - **Marcadores discursivos de abertura/mudança em pt-BR (`TOPIC_START_RE`):** `"cara, ontem"`, `"deixa eu te contar"`, `"sabe o que aconteceu"`, `"falando nisso"`, `"mudando de assunto"`, `"o problema é que"`, `"pera aí"`, `"você viu que"`, ou uma **pergunta direta (`?`)** seguida de pausa;
   - **Queda de similaridade semântica (Vale de Cosseno):** quando usando `paraphrase-multilingual-MiniLM-L12-v2` (ONNX INT8) com janela $k=2$ frases à esquerda vs $k=2$ à direita (medido na Pesquisa C em `10.4 ms/janela`, contraste $\Delta = 0.137$).
3. **Resolução do Problema dos Assuntos Longos (Decisões 3 e 5):**
   - Se a `TopicUnit` tem **`25s <= dur <= 60s`**: vira **1 clipe único completo** (do `start` da 1ª frase do assunto ao `end` da conclusão + reação, **sem exigir que o pico acústico esteja entre 35% e 75%**!).
   - Se a `TopicUnit` tem **`60s < dur <= 180s`** (história longa de até ~3 min): o módulo `split_long_topic()` divide a unidade em **$N \in \{2, 3\}$ partes de `40s–58s` (`Parte 1/N`, `Parte 2/N`...)** cortando exclusivamente em bordas de frase (`.?!` ou pausa `>= 0.5s`) que maximizem a tensão (*cliffhanger*: conectores adversativos `"mas aí"`, `"só que"`, `"quando de repente"`, ou pergunta antes da resposta).

### Passo 2.2 — Pontuação Multimodal Barata + LLM Local (0–10)

Para cada segmento candidato $S$, calculamos primeiro um **`CheapScore(S)` $\in [0, 1]$** instantâneo (custo `< 0.1 ms` por segmento, zero LLM) para ranquear e filtrar os top candidatos:

$$\text{CheapScore}(S) = 0.30 \cdot F_{\text{narr}}(S) + 0.25 \cdot F_{\text{prosodia}}(S) + 0.25 \cdot F_{\text{interacao}}(S) + 0.20 \cdot F_{\text{sinais}}(S)$$

Onde:
- **$F_{\text{narr}}(S)$ (Força Narrativa e Gancho no Texto):**
  - Presença de pergunta (`?`) ou exclamação (`!`) nas primeiras 2 frases (+0.35);
  - Densidade de verbos de ação/narrativa no passado (`"falei"`, `"olhou"`, `"correu"`, `"chegou"`, `"aconteceu"`) ou marcadores de história/opinião forte (`"verdade"`, `"mentira"`, `"absurdo"`, `"nunca"`, `"segredo"`, `"caralho"`, `"meu deus"`) (+0.40);
  - Penalidade de rascunho/preenchimento: excesso de palavras vazias ou leitura de donate/sub (`"obrigado pelo sub"`, `"valeu pelos bits"`, `"salve pro..."` -> **filtro negativo `-0.80`!**).
- **$F_{\text{prosodia}}(S)$ (Dinâmica Vocal Mesmo Sem Pico de Volume):**
  - **Variância de energia da fala (`std(rms_db_voiced)`):** mede entonação expressiva vs fala monótona, independente do volume absoluto;
  - **Cadência de fala (`words_per_second` entre `2.4` e `4.2` pal/s):** filtra trechos arrastados/silenciosos (`< 1.2` pal/s);
  - **Energia de reação pós-frase:** presença de bursts de `0.4s–2.5s` com energia na banda `1.2–3.8 kHz` (risada/aplauso) entre frases.
- **$F_{\text{interacao}}(S)$ (Troca de Turno / Diálogo):**
  - Número de pares `Pergunta (?) -> Pausa curta (0.2–0.8s) -> Resposta` e alternância de ritmo entre frases (captura debates e entrevistas).
- **$F_{\text{sinais}}(S)$ (Loudness / Chat / Heatmap quando existirem):**
  - Se houver chat ou heatmap, soma bônus; **se não houver (`weight=0`), não zera o score do segmento!**

Em seguida, os **Top-$M$ segmentos ($M = \min(16, 2 \times \text{max\_clips})$)** passam pelo **LLM local (`Qwen3-1.7B-Q4_K_M`, ~8s/segmento)**, que devolve `nota_llm` $\in [0, 10]$ + `titulo` + `legenda` + `hashtags`, compondo o score final:

$$\text{FinalScore}(S) = 0.55 \cdot \left(\frac{\text{nota\_llm}}{10}\right) + 0.45 \cdot \text{CheapScore}(S)$$

---

## 3. Custo de CPU: Como Evitar Transcrever 3 Horas Inteiras no `small` (`int8`)?

### O Problema Matemático
No piso (`Ryzen 5 5500U`, 6 threads), o `faster-whisper small` (`int8`, `beam_size=5`) roda a **`~2.05 xRT`** (`703s` para `24 min` de áudio em `docs/teste-real.md`).
- Transcrever um VOD de **3 horas (`180 min`) inteiro** no `small` levaria **`~88 minutos` (`5.275 s`)** — inviável para alto volume!

### Solução em Duas Passadas (Triagem Acústica + ASR Rápido `base`/`tiny` -> ASR Preciso `small`/`turbo`)

| Estratégia de Transcrição | O que processa em 1 Hora (`60 min`) de Vídeo | Tempo de CPU por Hora de Vídeo (`6 threads`) | Tempo p/ VOD de 3h (`180 min`) | RAM Pico (`RSS`) | Viabilidade no Piso |
|---|---|---:|---:|---:|---|
| **A. Ingênua: `small` (`int8`, `beam=5`) no vídeo inteiro** | 60 min completos | `~29.3 min` (`1758 s`) | `~88 min` | `~650 MB` | ❌ Lento demais p/ VODs longos |
| **B. `small` (`int8`, `beam=1` greedy + VAD)** em blocos filtrados por prosódia (Top 40% do vídeo = 24 min/h) | 24 min/h | **`~7.5 min` (`450 s`)** | **`~22.5 min`** | `~650 MB` | ✅ **Viável e zero modelo extra!** |
| **C. Duas Passadas (Recomendada p/ Lives Sem Picos): Pass 0 (Prosódia) + Pass 1 (`base` `int8`, `beam=1`) + Pass 2 (`small` `int8`, `word_timestamps=True` nos Top-K)** | Pass 1: 35 min/h de fala ativa no `base` (`~10 xRT`) -> Pass 2: 8 min/h de candidatos finais no `small` | **`~3.5 min` (Pass 1) + `~2.5 min` (Pass 2) = `~6.0 min / hora`** | **`~18 min`** | `~650 MB` | ✅ **Ótimo custo-benefício p/ podcasts de 3h!** |

#### Como funciona a **Passada 0 (Pré-Triagem Acústica de Prosódia Sem Picos, `< 1 s` por hora de áudio)**
Mesmo quando uma live não tem gritos (loudness plano), **nem todo minuto de uma live de 3h tem a mesma densidade de conteúdo**: há pausas, leitura de tela em silêncio, partidas concentradas sem fala, intervalos musicais.
Sem baixar nenhum modelo extra, dividimos o áudio `16 kHz` em janelas deslizantes de `30s` (passo `10s`) e calculamos 3 métricas em NumPy:
1. **Taxa de Voz Ativa (`voiced_fraction`):** fração de frames de `0.5s` com `rms_db > baseline_silence + 8 dB` (descarta silêncio/AFK);
2. **Modulação Silábica (`envelope_mod_4_8hz`):** variância das diferenças sucessivas de energia $|\text{db}[t] - \text{db}[t-0.5\text{s}]|$, que mede **fala animada e dinâmica de conversação** mesmo quando um compressor trava o volume máximo no mesmo teto dB!
3. **Variância de Pausa/Turno:** presença de contornos de frase (alternância entre fala densa e micro-pausas de respiração `0.5s–1.5s`, diferenciando conversa real de música contínua).

Selecionando o **Top 35%–40% do vídeo** por esse índice prosódico (`~20–24 min` por hora de VOD), o `faster-whisper small` (`int8`, `beam_size=1`) transcreve **1 hora de live calma em apenas ~7.5 minutos** (sem precisar sequer baixar o `base`), ou em **~6.0 minutos** usando `base` na Passada 1 + `small` na Passada 2!

---

## 4. Limiares Adaptativos, "Modo Piso" (Cota por Hora) e Diversificação Temporal

Hoje, `candidates.py:26` usa `threshold = floor + 0.08 * (peak_max - floor)` e `snap.py:312` descarta qualquer candidato cujo pico não caia em `[0.35, 0.75]`. Para garantir volume e qualidade estáveis:

1. **Percentil por Vídeo + Degradação Graciosa de `peak_ratio`:**
   - Se o sinal acústico combinado no candidato tiver proeminência alta (`raw_score >= P90` do vídeo e salto claro de dB/chat), usamos o modo `peak_anchored` com faixa relaxada **`PEAK_POS_MIN = 0.20, PEAK_POS_MAX = 0.88`** (permitindo que a risada fique no final da piada em `85%`!).
   - Se a curva de áudio for plana (podcast conversado: `p99(rms_db) - p50(rms_db) < 6.5 dB` ou candidato vindo de texto/prosódia), `snap_candidate` entra em **`edge_mode = "narrative"`**: **desliga a restrição `peak_ratio`** e escolhe o par `(t_start, t_end)` de bordas de frase que maximiza `CheapScore(S) + qualidade_de_borda_gramatical`.
2. **"Modo Cota / Piso" (`min_clips_per_hour = 3`, ajustável até `--max-clips`):**
   - Divide a duração total do vídeo $D$ em $B = \max(1, \lfloor D / 1200\text{s} \rfloor)$ **baldes temporais de 20 minutos**.
   - Garante que pelo menos os melhores candidatos de cada balde de 20 min sejam avaliados no ASR/Snap, impedindo que 100% dos clipes venham dos primeiros 15 minutos da live.
3. **Piso de Qualidade (Anti-Clipe Ruim):**
   - "Modo piso" **não** significa publicar silêncio ou música ambiente: o candidato só é aprovado se tiver `word_count >= 35 palavras` (~15s de fala real), `CheapScore >= 0.35` (ou `nota_llm >= 6/10`) e bordas em fim de frase/pausa (`mid_word_cut_rate == 0.0`).
4. **Deduplicação Semântica + Temporal (NMS Duplo):**
   - Além do NMS temporal (`overlap > 0s` ou distância `< 30s` para clipes independentes; partes `1/N` e `2/N` do mesmo assunto são permitidas como sequência rotulada!), descartar candidatos com **Jaccard de bigramas `>= 0.60`** (evita dois clipes onde o streamer repete a mesma frase/história minutos depois).

---

## 5. Como Calibrar Sem GPU (Conjunto Rotulado Leve + Precision@K)

Para calibrar os pesos de `CheapScore` e o prompt do LLM sem GPU e sem custo:

1. **Dataset Semente Imediato (`20 a 40 segmentos` a partir do próprio cache do projeto):**
   - Usar os **10 clipes já validados em `docs/teste-real.md`** (`8` do VOD Podpah `UBv07VGeD24` + `2` da live `cStJTQf9XUk`) como exemplos positivos reais, mais `15–20` janelas negativas/neutras extraídas do mesmo `words.json` / `audio.wav` do `UBv07VGeD24` (ex.: trechos de leitura de patrocinador, conversa fiada sem desfecho e cortes truncados).
2. **Rotulagem em Planilha/JSON Simples (`calib_labels.json`, 15 min de trabalho humano):**
   - Para cada trecho de 30–60s, anotar 3 campos:
     - `label`: `2` (ótimo/viral), `1` (aceitável), `0` (ruim/incompleto/chato);
     - `is_complete_topic`: `true/false` (se o assunto começa e termina sem cortar no meio);
     - `has_audio_peak`: `true/false`.
3. **Métricas de Calibração Offline (`clipes/eval.py`):**
   - **`Precision@K` ($K \in \{5, 8\}$):** fração dos top-$K$ escolhidos pelo pipeline que têm `label >= 1`.
   - **`NDCG@8` e `Spearman \rho`:** correlação entre `FinalScore` (`CheapScore + LLM`) e a nota humana (ou percentil do heatmap do YouTube nos VODs que possuem heatmap, usando o heatmap apenas como **gabarito de teste offline** — exatamente como faz o paper *RHAPSODY*, escondendo o heatmap do detector durante o teste!).
   - **`TopicCompletenessRate`:** % de clipes que começam após pausa/marcador de início e terminam em frase conclusiva sem conector pendente (`"mas"`, `"porque"`, `"aí"`, `"então"` na última palavra).

---

## 6. Desenho Concreto de Implementação no `clipes-lives`

### 6.1. O Que Muda em Cada Arquivo (Mudança Mínima e Cirúrgica)

1. **`clipes/signals.py` — Novo sinal contínuo `signal_prosody(rms_db, n)` (Zero Modelo):**
   - Calcula para cada segundo $t$ (em janelas de `15s`):
     $$\text{prosody}(t) = 0.50 \cdot \text{norm}\!\left(\text{std}_{15\text{s}}(\text{db}_{\text{voiced}})\right) + 0.50 \cdot \text{norm}\!\left(\text{mean}_{15\text{s}}(|\Delta \text{db}_{0.5\text{s}}|)\right)$$
   - Quando o vídeo é conversado (`p95(rms_db) - p50(rms_db) < 6.5 dB`) ou não tem `chat`/`heatmap`, `compute_signals` ativa automaticamente `"prosody"` com peso `0.35`, garantindo picos de **dinâmica de fala** mesmo com compressor de estúdio.

2. **`clipes/candidates.py` — Seleção Estratificada por Baldes (`pick_candidate_peaks`):**
   - Divide o vídeo em baldes de `20 min`.
   - Reserva `60%` das vagas (`limit = max_clips * 2`) para os maiores scores globais e **`40%` das vagas para o melhor ponto de cada balde de 20 min** (garantindo cobertura temporal uniforme quando não há picos extremos).
   - Para os top candidatos de conversa, amplia `window_half` de `45.0s` para **`75.0s` (janela de `150s`)** quando o sinal de prosódia ao redor permanece ativo, permitindo capturar histórias de até `2.5–3 min`.

3. **`clipes/snap.py` — Modo Narrativo + Divisão em Partes (`Parte 1/N`) + Fim de Frase Completo:**
   - **Relaxar `PEAK_POS_MIN / MAX`:** mudar de `(0.35, 0.75)` para **`(0.18, 0.88)`** no modo `peak`, e **desativar a trava de `peak_ratio`** quando `candidate.raw_score < 0.45` (modo `narrative`), pontuando pares `(t_s, t_e)` por:
     $$Q(t_s, t_e) = Q_{\text{start}}(t_s) + Q_{\text{end}}(t_e) + 4.0 \cdot \text{CheapNarrativeScore}(t_s, t_e) - 3.0 \cdot \mathbb{I}[\text{última palavra é conector suspenso}]$$
   - **Penalizar conectores suspensos no fim (`DANGLING_END_WORDS`):** proibir ou penalizar em `-3.0` cortes cuja última palavra antes da pausa seja `"mas"`, `"porque"`, `"que"`, `"então"`, `"aí"`, `"quando"`, `"tipo"`, `"né"` sem conclusão.
   - **Função `split_long_story(words, start_t, end_t, target_dur=50.0)`:** se o arco tem entre `65s` e `180s` sem queda temática, divide em 2 ou 3 clipes consecutivos (`40s–58s` cada) nas pausas/frases de maior tensão e preenche `clip.part_index = 1..N, clip.part_total = N`.

4. **`clipes/cli.py` e `clipes/render.py` — Integração LLM Local + Crédito/Encerramento:**
   - Após `select_clips` (e antes de `render_clip`), rodar o avaliador local (`Qwen3-1.7B-Q4_K_M` via `llama-cpp-python` se instalado/configurado, com fallback transparente para `generate_title` + `CheapNarrativeScore` se `--no-llm`).
   - O `render_clip` injeta no `.ass` o `CreditPill` (`@canal + URL`), a `Watermark`, o `PartBadge` (`PARTE 1/N` se `part_total > 1`) e o `OutroCard` sobre `tpad=stop_mode=clone:stop_duration=1.3` + `afade`.

### 6.2. Ordem Recomendada de Implementação

1. **Etapa 1 (Zero dependências novas, resolve Decisões 3, 4 e 5):**
   - Relaxar `PEAK_POS` em `snap.py` + adicionar penalidade de conector suspenso + `CheapNarrativeScore` léxico/pontuação + `split_long_story` (`Parte 1/N`);
   - Adicionar `signal_prosody` e amostragem por baldes temporais em `signals.py` / `candidates.py`;
   - Atualizar `render.py` com Safe Zones (`MarginV=590`), `CreditPill`, `PartBadge`, `tpad clone 1.3s` (`OutroCard`) e `afade`.
2. **Etapa 2 (ASR em 2 velocidades):**
   - Ajustar `asr.py` para `beam_size=1` na triagem das janelas (reduzindo o tempo de ASR em ~35%) e opção de refino com `large-v3-turbo` apenas nos clipes finais.
3. **Etapa 3 (Modelos leves em CPU):**
   - Adicionar módulo opcional `llm.py` (`Qwen3-1.7B-Q4_K_M` c/ ChatML + `/no_think` e `del model; gc.collect()`) para título, legenda, hashtags e nota `0–10` antes do render;
   - Adicionar `reframe.py` com `OpenCV YuNet` (`228 KB`) a `1 fps` para reenquadramento de rosto/webcam.
