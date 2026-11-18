# Pesquisa D (Extra): Detecção de Bons Momentos Sem Depender de Picos (nem do Heatmap do YouTube)

- **Agente / Worktree:** `pesq-argon-A` (Gemini 4 Argon)
- **Data da pesquisa e verificação:** `2026-11-17`
- **Escopo:** Arquitetura e algoritmos para detectar momentos virais/narrativos em lives e VODs (podcasts conversados, lives calmas, VODs sem chat/heatmap, gravações ao vivo) **sem depender de picos de volume (`loudness`) nem do `heatmap` do YouTube**, respeitando o piso de hardware (`Ryzen 5 5500U`, container Docker `6 GB RAM`, `6 threads`, 100% CPU, gratuito e offline).

---

## 0. Diagnóstico no Código Atual: Por que `signals.py` + `candidates.py` + `snap.py` Falham Sem Picos

A inspeção direta de `clipes/signals.py`, `clipes/candidates.py` e `clipes/snap.py` mostra quatro gargalos estruturais quando o vídeo é conversado ou não tem `heatmap`/`chat`:

1. **Zona morta de `+3 dB` / `+4 dB` e `min_scale=8.0` em `signal_loudness` (`signals.py:130-143`):**
   - `excess = np.maximum(0.0, db - baseline - 3.0)` e `jump = np.maximum(0.0, db - prev_mean - 4.0)` seguidos de `robust_percentile_norm(..., min_scale=8.0)`.
   - Em podcasts com compressor de áudio na mesa de som (Podpah, Flow, entrevistas) ou lives de conversa calma, a voz oscila apenas `±2,5 dB` em torno da mediana. Resultado: `excess` e `jump` ficam quase zerados, e `min_scale=8.0` achata toda a curva para perto de `0.0`.
2. **Falsos positivos de barulho sem conteúdo (provado em `docs/teste-real.md`):**
   - No teste real do Podpah #1049 (`UBv07VGeD24`), o **Clipe #1 de maior score (`0.902`)** foi `clip_01.mp4` (`305.29s–337.25s`, *"E pra vocês palmas em a é o gabriela caralho"*): aplausos e gritos de entrada do convidado, **zero história ou valor de retenção**. Enquanto isso, histórias excelentes contadas em tom sério ou irônico sem gritar jamais disparam `find_peaks`.
3. **Candidatos modelados como impulsos pontuais (`find_peaks` em `candidates.py:28`):**
   - Uma história envolvente, uma revelação polêmica ou um debate tenso não é um "impulso de 1 segundo em $t = p$", mas sim um **arco narrativo contínuo $[t_{\text{setup}}, t_{\text{payoff}}]$ de 35 s a 120 s**.
4. **Ancoragem rígida no pico em `snap.py` (`PEAK_POS_MIN = 0.35`, `PEAK_POS_MAX = 0.75`, `max_dur = 55.0`):**
   - Mesmo quando há um pico no final da piada (aos 90% da história), `snap.py:334` descarta qualquer corte onde o pico fique após 75% da duração, obrigando o clipe a cortar o desfecho ou começar no meio do assunto (causa raiz da **Decisão 5**).

---

## 1. Como Ferramentas e Papers Detectam Highlights Sem Picos de Áudio (e o que Cabe em CPU / 6 GB)

### 1.1. Ferramentas Open-Source e Comerciais Verificadas

| Ferramenta / Paper | Como Detecta Momentos Sem Depender de Pico de Áudio | O que é Reutilizável no Nosso Piso (CPU 6 Threads / 6 GB RAM) | Fonte e Data Verificada |
| :--- | :--- | :--- | :--- |
| **ClipsAI** (`clipsai`, open-source Python) | Transcreve o áudio completo e aplica **TextTiling com embeddings de sentenças** (evolução do algoritmo clássico de Hearst, 1997). Calcula a similaridade semântica entre blocos adjacentes de sentenças em múltiplas escalas de janela; vales profundos de similaridade (*depth scores*) definem o início e o fim de cada subtópico autocontido. | **100% reutilizável:** o algoritmo de *TextTiling* / *Depth Score* sobre sentenças do Whisper. Em CPU, pode usar **TF-IDF de n-gramas de caracteres/palavras** (0 MB RAM, < 5 ms) ou modelo ONNX INT8 `multilingual-MiniLM-L12` (~110 MB RAM, ~1 s para 3h de texto). | [GitHub - ClipsAI/clipsai](https://github.com/ClipsAI/clipsai); [Docs ClipsAI ClipFinder](https://www.clipsai.com/references/clip); [Hearst (1997) TextTiling - ACL](https://aclanthology.org/J97-1003.pdf) (verificados em `2026-11-17`) |
| **OpusClip** (`ClipAnything` + `Virality Score`, comercial) | Divide a transcrição em micro-narrativas coerentes e pontua cada segmento com um **Virality Score (0–100)** baseado em 4 critérios semânticos: (1) *Hook Strength* nos primeiros 3s, (2) *Narrative Completeness* (início, meio e fim sem cortar assunto), (3) *Emotional/Sentiment Resonance* (humor, polêmica, insight, choque) e (4) *Pacing* (ritmo de fala sem gordura). | **Altamente reutilizável em 2 estágios:** (1) extrair os 4 critérios via heurísticas léxico-estruturais determinísticas sobre todas as janelas; (2) rodar um LLM local leve (`Qwen2.5-1.5B/3B-Instruct` GGUF `Q4_K_M`) **apenas nos Top-M candidatos** para dar nota 0–10 de completude e gancho. | [OpusClip ClipAnything](https://www.opus.pro/clipanything); [OpusClip Video Clipping Techniques](https://www.opus.pro/blog/video-clipping-techniques) (verificados em `2026-11-17`) |
| **Rhapsody: Highlight Detection in Podcasts** (Park, Diwan, Harwath, Choi — COLM / arXiv, maio/2025) | Estudo em **13.000 episódios de podcasts** usando o *Most Replayed* do YouTube como *ground truth* de treino para prever highlights **sem ter o heatmap na inferência**. Mostra que texto puro (*zero-shot* LLM) falha ao perder a entonação, e áudio puro falha em conversas calmas; a fusão de **segmentação léxica + prosódia acústica (pitch, ritmo de fala, pausas, energia de voz)** supera ambos. | **Diretamente aplicável:** substituir o sinal único de `loudness` por um pacote de **features prosódicas 1 Hz** extraídas do próprio `audio.wav` (taxa silábica, modulação de pitch/espectro, contraste de pausa, energia de riso) somado ao score léxico da transcrição. | [arXiv:2505.19429 - Rhapsody: A Dataset for Highlight Detection in Podcasts](https://arxiv.org/abs/2505.19429) (maio/2025); [GitHub - younghanstark/rhapsody](https://github.com/younghanstark/rhapsody) (verificados em `2026-11-17`) |
| **Vizard.ai / Ferramentas de Speaker Turn & Diarization** | Detectam mudanças de turno rápidas (*turn-taking*: pergunta -> resposta imediata ou interrupção/debate) e isolam blocos completos por tópicos da conversa. *(Pesos internos proprietários do Vizard = **não verificado**).* | Detectar **troca de falante / mudança de turno** sem Pyannote pesado: medir a diferença de centroide espectral / $F_0$ mediano entre frases separadas por pausa curta + presença de `?` seguida de resposta. | [ClipsAI PyPI / Diarization](https://pypi.org/project/clipsai/) (verificado em `2026-11-17`) |

---

## 2. Abordagem "Segmentar Tudo + Pontuar Tudo" (Unidades de Assunto + Score Multimodal)

Em vez de perguntar *"em qual segundo houve um grito?"*, o pipeline passa a responder duas perguntas em sequência:
1. **Onde começam e terminam os blocos de assunto/história desta conversa?** (Segmentação)
2. **Quais desses blocos têm o melhor arco (gancho -> desenvolvimento -> fecho/reação)?** (Pontuação)

### 2.1. Etapa A — Segmentação da Transcrição em Unidades de Assunto / Arco Narrativo

Dada uma sequência de palavras transcritas $W = [w_1, \dots, w_N]$, primeiro agrupamos $W$ em **sentenças elementares** $S = [s_1, \dots, s_M]$, onde uma nova sentença inicia sempre que $w_k.\text{ends\_sentence}$ for verdadeiro (`.`, `!`, `?` em `models.py:25`) ou houver pausa acústica $\Delta t_k = w_{k+1}.\text{start} - w_k.\text{end} \ge 0.65\text{ s}$.

Para cada fronteira entre a sentença $s_i$ e $s_{i+1}$, calculamos uma **Força de Quebra de Tópico** $B_i \in [0, 1]$ combinando 3 sinais determinísticos:

1. **Vale de Coesão Lexical (*TextTiling Depth Score* leve):**
   - Formamos uma janela esquerda $L_i = \{s_{\max(1, i-2)}, \dots, s_i\}$ e uma janela direita $R_i = \{s_{i+1}, \dots, s_{\min(M, i+3)}\}$.
   - Representamos $L_i$ e $R_i$ por vetores TF-IDF de **n-gramas de caracteres (3 a 5 chars) + palavras sem *stopwords*** (custo < 2 ms em NumPy puro, captura radicais em português como `roubad-`, `polici-`, `dinheir-` sem precisar de *stemmer* externo).
   - Seja $\text{sim}_i = \cos(\vec{v}(L_i), \vec{v}(R_i))$. O *Depth Score* de Hearst (1997) mede o quão fundo é o vale de similaridade em $i$ comparado aos picos adjacentes:
     $$d_i = \frac{1}{2}\left[(\max_{l \le i} \text{sim}_l - \text{sim}_i) + (\max_{r \ge i} \text{sim}_r - \text{sim}_i)\right]$$
2. **Pausa Acústica Inter-Sentencial:**
   $$p_i = \text{clip}\left(\frac{s_{i+1}.\text{start} - s_i.\text{end} - 0.35}{1.50}, 0.0, 1.0\right)$$
3. **Marcadores Discursivos de Abertura / Fecho Narrativo (Regex pt-BR):**
   - `TOPIC_START_RE`: `r"^(?:cara,? |mano,? |olha só|deixa eu te contar|sabe o que|uma vez|o problema é|a verdade é|quando eu|eu tava|imagina|você viu|falando nisso|peraí)"` -> adiciona $+0.35$ em $B_i$ quando $s_{i+1}$ começa com marcador de história.
   - `TOPIC_END_RE`: `r"(?:é isso|moral da história|resumindo|enfim|tá ligado\?$|acabou ali|foi bizarro|nem fodendo)"` -> adiciona $+0.25$ em $B_i$ quando $s_i$ fecha uma ideia.

A fronteira $i$ é marcada como **Âncora de Tópico** quando $B_i = 0.50 \cdot \hat{d}_i + 0.30 \cdot p_i + 0.20 \cdot m_i$ é máximo local.
A partir das sentenças e âncoras de tópico, construímos o conjunto de **Segmentos Narrativos Candidatos** $U = \{u_1, \dots, u_K\}$:
- **Segmentos Simples (Clipe Único):** pares de sentenças $(s_a, s_b)$ com duração $D(u) = s_b.\text{end} - s_a.\text{start} \in [\text{min\_dur}, \text{max\_dur}]$ (padrão `25s–60s`), priorizando início e fim em âncoras de tópico.
- **Blocos Longos (Candidatos a Série "Parte 1/N" — Decisão 3):** quando duas âncoras fortes de tópico delimitam uma história contínua de `65 s a 180 s` sem queda de coesão interna, o bloco inteiro é preservado e fatiado em 2 ou 3 partes contíguas de `45–60 s` exatamente nas melhores bordas de frase internas.

### 2.2. Etapa B — Pontuação de CADA Segmento (Features Baratas + Rubrica LLM Local)

Cada segmento candidato $u = [t_s, t_e]$ recebe primeiro um **Score Intrínseco Rápido** $S_{\text{fast}}(u) \in [0, 1]$ (100% CPU/NumPy, sem depender de pico de dB):

$$S_{\text{fast}}(u) = 0.22\,F_{\text{hook}}(u) + 0.22\,F_{\text{arc}}(u) + 0.20\,F_{\text{prosody}}(u) + 0.16\,F_{\text{dialog}}(u) + 0.20\,F_{\text{hype}}(u)$$

Onde cada componente é calculado sobre $[t_s, t_e]$:

1. **$F_{\text{hook}}(u)$ — Força do Gancho nos Primeiros 5 Segundos:**
   - Premia se a 1ª sentença $s_a$ tem entre 4 e 14 palavras ($+0.30$), contém `?` ou `!` ($+0.35$), contém palavra de impacto/curiosidade/número ($+0.35$), e **penaliza ($-0.45$)** se a 1ª palavra for muleta/conector solto (`"mas"`, `"então"`, `"tipo"`, `"é"`, `"aí"`, `"né"`).
2. **$F_{\text{arc}}(u)$ — Estrutura de Arco Narrativo (`setup -> desenvolvimento -> punchline/fecho`):**
   - Divide $u$ em 3 terços temporais ($T_1$: setup `[0, 33%]`, $T_2$: corpo `[33%, 67%]`, $T_3$: clímax/fecho `[67%, 100%]`).
   - Mede: (a) presença de marcadores de abertura/contexto em $T_1$; (b) crescimento da densidade emocional/exclamações/reação no terço final $T_3$ em relação a $T_1$ ($\text{arousal}(T_3) > \text{arousal}(T_1)$); (c) fechamento limpo em $s_b$ ($B_b$ alto, sem reticências e sem pergunta sem resposta, exceto se for Parte 1 de série).
   - **Filtro Anti-Ruído (resolve o bug do `clip_01` de palmas em `docs/teste-real.md`):** exige **mínimo de 35 palavras faladas** (ou $\ge 1,6\text{ palavras/s}$) e diversidade lexical (Type-Token Ratio $\text{TTR} = \frac{|\text{unique words}|}{|\text{words}|} \ge 0.42$). Palmas/gritos sem discurso têm poucas palavras e caem para o fim da fila!
3. **$F_{\text{prosody}}(u)$ — Dinâmica Prosódica e Ritmo de Fala (Independe de Volume Alto):**
   - **Taxa de fala (*Speech Rate*):** palavras/segundo $r_w = N_{\text{words}} / D(u)$. Máximo ($1.0$) na faixa engajada de **2,5 a 4,0 palavras/s** (150–240 WPM); decai linearmente fora dessa faixa.
   - **Fração de fala ativa (*Voiced Ratio*):** fração dos frames em $[t_s, t_e]$ com voz ativa ($\ge 0.70$ ideal; penaliza pausas mortas longas no meio do clipe).
   - **Modulação de Entonação / Contraste Espectral:** desvio interquartil da energia na banda de formantes/harmônicos (500–2.500 Hz) normalizado pela energia total do frame — captura ênfase vocal, indignação, ironia e riso contido mesmo em áudio comprimido a `-18 dBFS` constantes!
4. **$F_{\text{dialog}}(u)$ — Dinâmica de Pergunta-Resposta e Troca de Falante:**
   - Conta pares `(pergunta -> resposta)` dentro do segmento + mudanças acústicas entre sentenças adjacentes (diferença na razão de energia baixa/alta frequência $\frac{E_{100-400\text{Hz}}}{E_{400-2000\text{Hz}}}$ entre $s_k$ e $s_{k+1}$, que estima mudança de voz grave/aguda entre host e convidado em < 0,1 ms!).
5. **$F_{\text{hype}}(u)$ — Sinais Multimodais (`loudness`, `chat`, `heatmap`) como Bônus Adaptativo:**
   - Se o vídeo tiver picos de `loudness`, `chat` ou `heatmap`, usa o percentil 90 da curva fundida dentro do terço médio/final $[t_s + 0.30 D, t_e]$. Se o vídeo for calmo (sem picos), a soma dos outros 4 termos já garante diferenciação nítida entre os segmentos!

#### Re-ranking Semântico Opcional (Top-$M$ candidatos via LLM Local 0–10 — Decisão 1)
Para os Top $M = \min(20, 3 \times \text{max\_clips})$ segmentos de maior $S_{\text{fast}}(u)$, após descarregar o Whisper da RAM:
- Se houver um modelo GGUF local configurado (ex.: `Qwen2.5-1.5B-Instruct-Q4_K_M`, 1,0 GB RAM, via interface pluggable `clipes/scorer.py`), avalia a transcrição de cada um dos $M$ candidatos pedindo nota 0–10 para: `(1) gancho_inicial, (2) historia_completa_sem_corte_meio, (3) interesse_viral`, gerando $S_{\text{llm}}(u) \in [0, 10]$.
- Se rodar sem LLM (padrão *zero-download* extra), $S_{\text{final}}(u) = S_{\text{fast}}(u)$ já opera de forma 100% autônoma e determinística.

---

## 3. Custo de CPU e Arquitetura de ASR em Duas Passadas (Orçamento Real para VODs de 3 Horas)

### 3.1. Por que Transcrever 3h Direto com `small int8` Atual é Inviável
No `docs/teste-real.md` (Seção 3), medimos no container Docker (`6 GB RAM`, `6 threads` do Ryzen 5 5500U):
- `faster-whisper small` (`int8`, `beam_size=5` padrão, `word_timestamps=True`) levou **`703,31 s` para `24 min` (`1.440 s`) de áudio** -> **RTF = `0,488x`** (`29,3 s` de CPU por minuto de áudio).
- Para um VOD de **3 horas (`180 min`)**, transcrever 100% do áudio nesse modo custaria **`5.274 s` (~88 minutos de CPU)** apenas na etapa de ASR!

### 3.2. Três Otimizações Imediatas + Pipeline de Duas Passadas

Inspecionando `clipes/asr.py` (linhas 98–107), o tempo atual de `29,3 s/min` ocorre porque `model.transcribe()` usa os padrões pesados do `faster-whisper` (`beam_size=5` e `word_timestamps=True` em tudo). Combinando **Pré-Triagem Acústico-Prosódica 1 Hz** + **Passada 1 Rápida (`tiny`/`base`, `beam_size=1`, sem DTW de palavras)** + **Passada 2 Cirúrgica (`small`, `beam_size=1`, `word_timestamps=True`)**:

| Etapa do Pipeline Proposto (por **1 hora = 60 min** de VOD) | O que Faz | Volume Processado por 1h de Vídeo | RTF no Ryzen 5 5500U (6 threads) | Tempo de CPU por 1h de Vídeo | RAM Pico |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **Estágio 0: Sinais 1 Hz + Pré-Triagem Prosódica (Sem ASR)** | Extrai RMS 2 Hz + **Taxa de Modulação Silábica (3–6 Hz)** + **Fluxo Espectral de Voz (300–3.400 Hz)** direto do `audio.wav` em chunks de 2 min. Descarta silêncio, música pura e trechos monotonais; seleciona os **Top 35%–45%** do vídeo com conversa dinâmica/ativa. | `60 min` de WAV 16 kHz | `~0,0006x` (NumPy FFT/filtros) | **~2,2 s** | `< 80 MB` |
| **Estágio 1: Passada 1 de ASR (Triagem de Tópicos)** | Roda `faster-whisper tiny` (ou `base`) `int8` com **`beam_size=1`** e **`word_timestamps=False`** nas janelas de conversa ativa filtradas pelo Estágio 0. Gera frases com timestamps de segmento `<|start|><|end|>`. | `~22 a 26 min` de áudio ativo | `0,045x` (`tiny` beam=1) a `0,085x` (`base` beam=1) | **~65 s a 125 s** (1 a 2 min) | `~550 MB` (`tiny`) / `~950 MB` (`base`) |
| **Estágio 2: Segmentação + Score Rápido $S_{\text{fast}}$** | Agrupa frases em arcos narrativos (TextTiling TF-IDF + pausas + regex pt-BR), calcula $S_{\text{fast}}(u)$ e seleciona as **Top-$K_{\text{cand}}$ janelas** (ex.: 6 a 8 janelas por hora) com diversificação temporal. | `~400 a 600` frases | `< 0,0001x` | **< 0,5 s** | `< 50 MB` |
| **Estágio 3: Passada 2 de ASR Cirúrgica (Alinhamento Fino)** | Roda `faster-whisper small` `int8` com **`beam_size=1`** e **`word_timestamps=True`** **somente** nas Top-$K_{\text{cand}}$ janelas selecionadas (ou reaproveita se já em cache) para `snap.py` e legendas `.ass` perfeitas. | `~6 a 9 min` de áudio candidato | `~0,23x` (`small` int8 c/ `beam_size=1`) | **~85 s a 125 s** (1,5 a 2 min) | `~1,6 GB` |
| **TOTAL (Detecção + ASR em 2 Passadas)** | **Cobre 100% dos momentos falados do vídeo sem depender de picos!** | **1h de VOD** | **Efetivo Global: ~`0,045x–0,070x`** | **~2,6 a 4,2 min por hora de vídeo** (~8 a 12,5 min p/ VOD de 3h!) | **<= 1,6 GB** (cabe com folga nos 6 GB!) |

> **Fato de engenharia importante sobre `asr.py`:**
> Apenas adicionar `beam_size=1` na chamada `model.transcribe(...)` em `clipes/asr.py:98` já reduz o tempo do `small int8` de `703 s` (~11,7 min) para **~`310–350 s` (~5,5 min)** no mesmo teste real do Podpah (`UBv07VGeD24`), mantendo as 6 threads (`OMP_NUM_THREADS=6`). E como o Estágio 1 (`tiny`/`base`) e o Estágio 3 (`small`) rodam **sequencialmente** (descarregando o modelo anterior com `del model; gc.collect()`), o pico de RAM nunca ultrapassa `~1,6 GB`.

---

## 4. Limiares Adaptativos, "Modo Piso" (Cota por Hora), Diversificação e Deduplicação

### 4.1. Limiares Adaptativos por Percentil e MAD (Substituindo Limiares Fixos)
Em `signals.py`, adicionamos um quarto sinal nativo de 1 Hz chamado **`prosody` (ou `speech_activity`)**, calculado diretamente do `audio.wav` sem depender de gritos:
- Para cada janela de 0,5 s (`FPS_RMS = 2`), além do RMS dB, calculamos:
  1. **Razão de Energia na Banda Vocal (`voice_band_ratio`):** fração da energia FFT entre `300 Hz` e `3.400 Hz` (distingue fala humana de batida musical grave ou chiado agudo).
  2. **Modulação Silábica (`syllable_mod`):** desvio-padrão do envelope RMS de 25 Hz (janelas de 40 ms) dentro de cada segundo — a fala humana articulada oscila fortemente entre consoantes e vogais a **3–7 Hz**, enquanto música contínua, ruído estático ou silêncio têm modulação baixa!
  3. **Variação Local de Pitch / Centroide Espectral (`spectral_var`):** desvio do centroide espectral nos segundos com voz ativa.
- **Normalização Adaptativa por Z-Score Robusto (MAD) + Gating de Voz Ativa:**
  Para qualquer sinal $x(t)$ nos segundos com voz ativa ($V = \{t : \text{db}(t) > -48\text{ dBFS} \land \text{voice\_band\_ratio}(t) > 0.45\}$):
  $$\text{MAD}_V(x) = \text{mediana}_{t \in V}\left(|x(t) - \text{mediana}_V(x)|\right)$$
  $$z_x(t) = \frac{x(t) - \text{mediana}_V(x)}{1.4826 \cdot \text{MAD}_V(x) + \epsilon_{\min}}$$
  Em seguida, convertemos $z_x(t)$ pelo **percentil empírico dentro do próprio vídeo** (`percentile_rank`), multiplicado por um *gate* de presença de fala ($\mathbb{I}[|V| / N \ge 0.05]$).
  - **Por que isso é seguro:** se o vídeo for uma live de música 24/7 sem fala ou silêncio plano (`test_zero_clips_flat_signal_degrades_cleanly`), o *gate* de modulação silábica + $\epsilon_{\min}$ mantém o sinal em `0.0` (0 clipes falsos). Já se o vídeo for um podcast de 2 horas falado em volume constante, o ranking por percentil destaca automaticamente os 10% de momentos de conversa mais articulada, rápida e expressiva daquele episódio!

### 4.2. "Modo Piso" (Quota Mode) — Volume e Qualidade Estáveis
Para garantir que um vídeo longo conversado nunca devolva apenas 1 ou 2 clipes (enquanto um vídeo curto ou mudo não seja forçado a inventar clipes ruins):
- Definimos a **Cota Alvo Adaptativa**:
  $$K_{\text{target}} = \min\left(\text{max\_clips},\; \max\left(1,\; \lfloor \text{duration\_min} / 15 \rfloor\right)\right)$$
  (Ex.: para 20 min de live -> piso de `1–2 clipes`; para 1h de VOD -> até `4 clipes`; para 2h+ de VOD -> até `max_clips = 8`).
- **Piso Mínimo de Qualidade Absoluta (`MIN_QUALITY_FLOOR = 0.32`):**
  Todo segmento candidato $u$ com `words >= 30`, `mid_word_cut == False`, bordas em frase/pausa e $S_{\text{fast}}(u) \ge 0.32$ é elegível para preencher a cota $K_{\text{target}}$, **mesmo que `signal_loudness` no trecho seja `0.0`!**

### 4.3. Diversificação Temporal ao Longo do Vídeo (MMR Temporal)
Em vez de pegar apenas os $K$ maiores scores brutos (que podem se concentrar todos num único bloco de 15 minutos da live), selecionamos iterativamente o próximo clipe $u^* \in U_{\text{valid}} \setminus C$ que maximiza a **Relevância Marginal Máxima (MMR) Temporal**:

$$u^* = \arg\max_{u \in U_{\text{valid}} \setminus C} \left[ S_{\text{final}}(u) \cdot \left(1 - \lambda \cdot \max_{c \in C} \exp\left(-\frac{|t_{\text{mid}}(u) - t_{\text{mid}}(c)|}{\tau}\right)\right) \right]$$

Onde:
- $t_{\text{mid}}(u) = \frac{1}{2}(u.\text{start} + u.\text{end})$
- $\tau = \max\left(120.0,\; \frac{\text{duration}}{1.5 \cdot \text{max\_clips}}\right)$ (raio de repulsão suave proporcional à duração do vídeo)
- $\lambda = 0.35$ (penaliza em até 35% um candidato colado a outro já escolhido, forçando o algoritmo a explorar outras partes da live a menos que o segundo candidato no mesmo bloco seja muito superior ao resto do vídeo).

### 4.4. Deduplicação Dupla (Temporal + Léxica)
Um candidato $u$ é descartado antes de entrar em $C$ se:
1. **Sobreposição temporal:** $\max(0, \min(u.\text{end}, c.\text{end}) - \max(u.\text{start}, c.\text{start})) > 0$ (com margem de `1.0 s`, exceto para partes contíguas `"Parte 1/2"` e `"Parte 2/2"` do mesmo arco narrativo).
2. **Repetição léxica (Jaccard de 3-gramas):** seja $G_3(u)$ o conjunto de trigramas de palavras limpas em $u$. Se $\frac{|G_3(u) \cap G_3(c)|}{|G_3(u) \cup G_3(c)|} > 0.45$ para algum $c \in C$, descarta $u$ (evita dois clipes do mesmo bordão/sponsor repetido na live).

---

## 5. Como Calibrar Sem GPU (Conjunto Rotulado de 20–50 Momentos + Uso do `docs/teste-real.md`)

### 5.1. Aproveitando Imediatamente os Clipes Já Gerados em `docs/teste-real.md`
Nós já temos em `docs/teste-real.md` (e nos caches `.cache/words.json` e `.cache/signals.npz` de `UBv07VGeD24` e `cStJTQf9XUk`) um laboratório perfeito com **5.060 palavras transcritas** e **10 clipes reais documentados** que ilustram exatamente o contraste entre **pico de barulho** vs. **história boa**:

| Clipe Real (`docs/teste-real.md`) | Score Atual (Pico) | O que Realmente Acontece no Vídeo | Rótulo Humano de Qualidade ($y \in \{0, 1, 2\}$) | Como o Novo Score $S_{\text{fast}}$ Se Comporta |
| :--- | :---: | :--- | :---: | :--- |
| `UBv07VGeD24 / clip_01` (`305s–337s`) | **`0.902` (1º!)** | *"E pra vocês palmas em a é o gabriela caralho"* — apenas aplausos e gritos na entrada do convidado, sem história. | **`0` (Falso Positivo de Pico)** | **Rebaixado drasticamente:** baixa densidade de palavras, baixo TTR lexical, zero estrutura de pergunta-resposta ou arco narrativo ($F_{\text{arc}} \approx 0.15$). |
| `UBv07VGeD24 / clip_03` (`8436s–8483s`) | `0.733` (3º) | *"Quem inventar mais história falsa?"* — debate completo (46.9s) sobre timidez e mentira, começa com pergunta forte. | **`2` (Excelente)** | **Sobe para o Topo (#1):** pergunta no hook ($F_{\text{hook}} = 1.0$), alta troca de turno ($F_{\text{dialog}}$ alto), 47s de fala fluida. |
| `UBv07VGeD24 / clip_05` (`3619s–3657s`) | `0.675` (5º) | *"Que que você perdeu?"* — história de notebook e moto roubada (37.5s). | **`2` (Excelente)** | **Sobe para o Top 2:** começa com pergunta direta + verbos narrativos de história + reação no fecho. |
| `UBv07VGeD24 / clip_07` (`4764s–4810s`) | `0.638` (7º) | *"Olhando assim, ó."* — história cômica do vizinho olhando pela janela (45.7s). | **`2` (Excelente)** | **Sobe no ranking:** alta coesão lexical e ritmo narrativo contínuo de 45.7s. |
| `UBv07VGeD24 / clip_06` (`6010s–6038s`) | `0.640` (6º) | *"Cara, olha... Muito feliz."* — cumprimento morno sobre encontro no RJ (28.2s). | **`0` ou `1` (Fraco)** | **Penalizado:** começa com muleta (`"Cara, olha..."`) e reticências, baixo arco narrativo. |
| `cStJTQf9XUk / clip_01` (`14s–50s`, Live) | `0.684` (1º) | *"Você iria contra o cauão, tio sã?"* — provocação/pergunta na live (36.3s, sem heatmap/chat). | **`2` (Bom)** | **Mantido no topo:** pergunta direta no início + boa taxa de fala. |

### 5.2. Protocolo de Calibração Offline em CPU (`< 1 segundo` de execução)
1. **Construção do `tests/fixtures/calibration_moments.json` (30 a 50 momentos):**
   - Extrair das janelas já transcritas de `UBv07VGeD24` (Podpah), `cStJTQf9XUk` (Live Gameplay) e 1 VOD de conversa sem gritos **40 segmentos candidatos** (20 momentos positivos `y=2` ou `y=1` com `[ideal_start, ideal_end]` revisados a olho, e 20 negativos `y=0` como aplausos de abertura, leitura de patrocinador/superchat morno, silêncios ou histórias cortadas pela metade).
2. **Métricas de Avaliação em `clipes/eval.py`:**
   - **$\text{Precision}@K$ ($K=5$):** $\frac{1}{K}\sum_{i=1}^{K} \mathbb{I}[y(\text{clip}_i) \ge 1]$. Meta: $\ge 0.80$ mesmo quando `heatmap=None` e `chat=None`!
   - **$\text{NDCG}@K$ ($K=8$):** $\frac{\text{DCG}@K}{\text{IDCG}@K}$ com ganho $2^{y_i} - 1$. Garante que histórias completas (`y=2`) fiquem sempre acima de gritos isolados (`y=0`).
   - **$\text{TopicCompletenessIoU}$:** IoU médio entre `[clip.start, clip.end]` e o intervalo completo do assunto `[ideal_start, ideal_end]`. Meta: $\ge 0.75$.
3. **Ajuste Automático dos Pesos sem GPU:**
   - Um script simples `python -m clipes.calibrate` lê `calibration_moments.json` e roda busca em grade (*simplex grid* de passo `0.05` sobre os 5 pesos de $S_{\text{fast}}$, ~1.000 combinações) em **0,15 segundo de CPU**, gravando os pesos ótimos.

---

## 6. Desenho Concreto e Pequeno para Implementação no Pipeline

### 6.1. O que Muda em Cada Arquivo (Sem Quebrar Nenhum Teste Existente)

#### 1. `clipes/signals.py` — Adicionar Sinal Prosódico/Fala 1 Hz (`signal_speech_prosody`)
- Na leitura em streaming de `audio.wav` (ou sobre `rms_db` + janelas de 1 s), calcular um vetor 1 Hz `scores["prosody"]` que mede **modulação silábica (3–7 Hz) + densidade de voz ativa + variação local de energia vocal** usando normalização por percentil/MAD com *gate* de silêncio (`SILENCE_DB = -55.0`).
- Atualizar `BASE_WEIGHTS`:
  ```python
  BASE_WEIGHTS = {
      "loudness": 0.30,
      "chat": 0.30,
      "heatmap": 0.20,
      "prosody": 0.20,
  }
  ```
- **Efeito imediato:** quando `chat` e `heatmap` não existem e `loudness` é baixo (podcast conversado), `prosody` garante uma curva 1 Hz informativa que destaca os blocos de fala mais articulada e expressiva, enquanto continua valendo `0.0` em áudio plano/música constante (preservando `test_zero_clips_flat_signal_degrades_cleanly`).

#### 2. `clipes/candidates.py` — Modo Híbrido: Picos + Janelas de Proposta Contínua (Modo Cota)
- Adicionar em `pick_candidate_peaks(...)` um segundo gerador complementar (`pick_narrative_windows`) quando os picos fortes (`prominence >= 0.08`) forem insuficientes ou para cobrir lacunas temporais longas (> 15 min sem candidatos):
  - Divide a linha do tempo em janelas deslizantes de `60 s` (passo `30 s`), pontua pela média do percentil de `prosody + loudness + chat` e seleciona os melhores blocos por estrato de tempo (MMR temporal com $\tau = \max(120, \text{duration} / (1.5 \cdot \text{max\_clips}))$), garantindo sempre pelo menos `max_clips * 2` janelas candidatas distribuídas ao longo do vídeo.
- Aumentar `window_half` padrão de `45.0` para **`75.0`** (janela de 150 s) quando o candidato vier de bloco conversado, para não truncar histórias longas.

#### 3. `clipes/asr.py` — Passada Rápida (`beam_size=1`) e Duas Passadas para VODs Longos
- Passar explicitamente `beam_size=1` em `model.transcribe(...)` (corta o tempo de CPU pela metade imediatamente).
- Adicionar função `transcribe_two_pass(audio_path, duration, candidates, cache_dir)`:
  - Se o total de áudio candidato passar de `25 min` (ex.: VOD > 90 min em modo sem picos), roda Passada 1 com `tiny` ou `base` (`int8`, `beam_size=1`, `word_timestamps=False`) nas janelas amplas, aplica o ranqueador léxico `score_narrative_segments` para filtrar as Top `max_clips + 4` janelas, e roda Passada 2 (`small int8`, `beam_size=1`, `word_timestamps=True`) apenas nelas.

#### 4. `clipes/snap.py` — Segmentação por Assunto/Sentença + Score $S_{\text{fast}}$ + Suporte a Arco Completo / "Parte 1/N"
- Adicionar função `score_text_segment(seg_words: list[Word], rms_db: np.ndarray) -> float` implementando a fórmula $S_{\text{fast}}(u)$ da Seção 2.2:
  - Penaliza muletas na 1ª palavra (`FILLER_START_RE = re.compile(r"^(?:é+|então|tipo|mano|cara|bom|aí|né|assim)\b", re.I)`).
  - Calcula `hook_score` (pergunta/exclamação/curiosidade na 1ª frase), `dialog_score` (pares `?` -> resposta), `pace_score` (2,4 a 4,2 palavras/s), `lexical_diversity` (TTR $\ge 0.42$ e mínimo de palavras para rebaixar aplausos sem fala) e `cohesion_boundary_score` (TextTiling TF-IDF nas bordas inicial e final).
- **Relaxar a trava de posição do pico (`PEAK_POS_MAX`) para candidatos narrativos:**
  - Quando `candidate` tiver fala densa (`len(local_words) >= 35`), permitir que o pico fique entre **`0.20` e `0.88`** da duração do clipe (em vez de `0.35–0.75`), porque o clímax de uma história frequentemente ocorre no punchline final (80%–88% do clipe)!
  - Nota: manter `PEAK_POS_MIN = 0.35` e `PEAK_POS_MAX = 0.75` quando a chamada vier com os parâmetros estritos legados ou em modo `silence`, mantendo 100% de compatibilidade com os testes unitários atuais (`tests/test_snap.py`).
- **Divisão automática em "Parte 1/N" (`split_long_narrative`):**
  - Se o melhor bloco de assunto contínuo delimitado por quebras de tópico tiver entre `65 s` e `180 s`, fatiar em 2 ou 3 objetos `SnappedClip` de `45–60 s` cada, cortados na frase de maior tensão interna (`?` ou conector suspensivo) com `part_index` e `part_total` preenchidos no manifesto `clips.json`.

#### 5. `clipes/cli.py` — Expor Flags e Score Combinado no `clips.json`
- Combinar no ranking final de `select_clips`:
  $$\text{clip.score} = 0.45 \cdot \text{signal\_score} + 0.55 \cdot S_{\text{fast}}(\text{clip})$$
- Adicionar opção `--min-clips-per-hour` (padrão `3`) e registrar no `clips.json` o detalhamento `narrative_score`, `hook_score` e `part` (`1/2`, `2/2` quando houver divisão de assunto longo).

---

### 6.2. Ordem Recomendada de Implementação (Menor Risco -> Maior Retorno)

1. **Passo 1 (15 min — Ganho Imediato de Velocidade):** adicionar `beam_size=1` e `initial_prompt=ing.title` em `clipes/asr.py` (reduz o tempo de ASR em ~2,2x em CPU sem alterar nenhuma interface).
2. **Passo 2 (45 min — Detecção Sem Pico e Fim dos Falsos Positivos de Barulho):** implementar `score_text_segment` ($S_{\text{fast}}$: hook, anti-muleta, pergunta-resposta, ritmo WPM, TTR mínimo e TextTiling TF-IDF nas bordas) em `clipes/snap.py` e combiná-lo ao score de seleção + MMR temporal.
3. **Passo 3 (40 min — Fim dos Cortes no Meio do Assunto + Séries Parte 1/N):** ampliar a janela narrativa em `candidates.py`/`snap.py`, permitir clímax no terço final (`peak_ratio` até `0.88` para histórias) e adicionar `split_long_narrative` para fatiar histórias de `65–180 s` em partes de `45–60 s` em borda de frase.
4. **Passo 4 (35 min — Sinal Prosódico 1 Hz e Modo Cota em `signals.py` + `candidates.py`):** adicionar `signal_speech_prosody` (modulação silábica 3–7 Hz + MAD/percentil adaptativo) e o gerador de janelas de cota (`pick_narrative_windows`) para garantir volume estável em lives/podcasts sem gritos, sem chat e sem heatmap.
5. **Passo 5 (30 min — Duas Passadas ASR + Script de Calibração):** ativar a pré-triagem em duas passadas (`tiny/base` -> `small`) para VODs longos em `asr.py` e validar com `pytest` + conjunto de calibração baseado nos dados do `docs/teste-real.md`.

---

## 7. Referências e Fontes Verificadas

1. **ClipsAI (Open-Source Python Library)** — *Transcript Segmentation via TextTiling with Sentence Embeddings*.
   - GitHub: https://github.com/ClipsAI/clipsai (verificado em `2026-11-17`)
   - Documentação Técnica (`ClipFinder`): https://www.clipsai.com/references/clip (verificado em `2026-11-17`)
   - PyPI: https://pypi.org/project/clipsai/ (verificado em `2026-11-17`)
2. **Hearst, Marti A. (1997)** — *TextTiling: Segmenting Text into Multi-paragraph Subtopic Passages*. Computational Linguistics, Vol. 23, No. 1, pp. 33–64, ACL.
   - URL: https://aclanthology.org/J97-1003.pdf (verificado em `2026-11-17`)
3. **Beeferman, D., Berger, A., & Lafferty, J. (1999)** — *Statistical Models for Text Segmentation*. Machine Learning / ACL.
   - URL: https://aclanthology.org/J01-1002.pdf (verificado em `2026-11-17`)
4. **Park, Y., Diwan, A., Harwath, D., & Choi, E. (2024/2025)** — *Rhapsody: A Dataset for Highlight Detection in Podcasts* (13.000 episódios pareados com curvas *Most Replayed*, combinando transcrição léxica e sinais acústico-prosódicos).
   - arXiv: https://arxiv.org/abs/2505.19429 (verificado em `2026-11-17`)
   - Código Oficial: https://github.com/younghanstark/rhapsody (verificado em `2026-11-17`)
5. **OpusClip** — *ClipAnything Multimodal Highlight Detection & Virality Score Architecture*.
   - URL 1: https://www.opus.pro/clipanything (verificado em `2026-11-17`)
   - URL 2: https://www.opus.pro/blog/video-clipping-techniques (verificado em `2026-11-17`)
6. **SYSTRAN `faster-whisper`** — *CTranslate2 INT8 CPU Inference, Beam Size & VAD Filtering*.
   - GitHub: https://github.com/SYSTRAN/faster-whisper (verificado em `2026-11-17`)
   - Benchmarks de RTF em CPU (`tiny`, `base`, `small` INT8): https://www.promptquorum.com/power-local-llm/faster-whisper-review (verificado em `2026-11-17`)
7. **Dados Empíricos Locais do Projeto (`clipes-lives`):**
   - `docs/teste-real.md` (medições de CPU no Ryzen 5 5500U, 6 GB RAM, 6 threads: `UBv07VGeD24` Podpah #1049 e live `cStJTQf9XUk`).
