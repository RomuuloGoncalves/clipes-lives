# Pesquisa D (Extra) — Detecção de Bons Momentos Sem Depender de Picos de Áudio nem do Heatmap do YouTube

- **Data da pesquisa:** 2026-11-17
- **Agente / Worktree:** `pesq-argon-B` (Gemini 4 Argon)
- **Escopo:** Investigação crítica (código atual + literatura científica + ferramentas open-source/comerciais) para garantir volume e qualidade estáveis de clipes em **podcasts conversados, lives calmas, VODs sem chat/heatmap e streams da Twitch**, respeitando o piso de hardware (**Ryzen 5 5500U, container Docker com 6 GB RAM, 6 threads, sem GPU, modelos pesados estritamente sequenciais**).

---

## 0. Diagnóstico no Código Atual: Por que Faltam Clipes Sem Picos/Heatmap?

Analisando `clipes/signals.py`, `clipes/candidates.py` e `clipes/snap.py`, identificamos três gargalos estruturais que causam sub-produção (ou zero clipes) quando não há `heatmap` nem `chat` e a fala é conversada:

1. **Zona morta de dB e `min_scale=8.0` em `signal_loudness` (`clipes/signals.py:118-147`):**
   - `excess = np.maximum(0.0, db - baseline - 3.0)` exige que a voz ultrapasse a mediana móvel de 60 s em **mais de +3,0 dB**, e `jump` exige salto abrupto de **+4,0 dB** em 3 s.
   - Em podcasts de estúdio (ex.: Podpah, Flow) e lives bem configuradas no OBS, o streamer usa **compressor/limiter** no microfone, mantendo a fala contínua dentro de uma faixa estreita de $\pm 2,0\text{ dB}$ em torno da mediana.
   - Como resultado, uma história excelente contada sem gritar produz `excess == 0` e `jump == 0`. E mesmo quando há inflexões de $1\text{–}2\text{ dB}$, a normalização `robust_percentile_norm(raw_1hz, low_q=50.0, high_q=99.0, min_scale=8.0)` divide por `8.0`, esmagando o sinal para próximo de zero.
2. **Seleção puramente pontual em `pick_candidate_peaks` (`clipes/candidates.py:11-68`):**
   - Usa `scipy.signal.find_peaks` buscando máximos locais de 1 segundo com `distance=90`. Se a curva for plana ou tiver poucos picos isolados, retorna menos candidatos do que `max_clips` (ou `[]`), sem nenhum mecanismo de fallback ("modo piso").
3. **Invariante rígida de clímax central em `snap_candidate` (`clipes/snap.py:16-17, 311-313`):**
   - Impõe `0.35 <= (peak - start_t) / dur <= 0.75`. Em cortes narrativos (histórias, debates, *hot takes*, perguntas e respostas), o momento mais forte frequentemente é o **gancho inicial** (primeiros 10% do clipe) ou a **punchline/conclusão** (últimos 10%–15% do clipe) — ambos hoje descartados por `snap.py`!

---

## 1. Como Ferramentas e Papers Escolhem Momentos Sem Depender de Pico de Áudio

### 1.1. Ferramentas Open-Source e Comerciais

| Ferramenta / Fonte | Como Detecta Momentos Sem Pico de Volume | O que é Reutilizável em CPU (6 GB / 6 Threads)? |
|---|---|---|
| **ClipsAI** *(Open Source, GitHub `ClipsAI/clipsai`)* | Transcreve o áudio com timestamps de palavras (WhisperX) e aplica o algoritmo **TextTiling** (Hearst, 1997) sobre embeddings de sentenças para detectar **fronteiras de mudança de tópico** em múltiplas granularidades, isolando blocos coesos de início e fim de assunto sem olhar para o volume. | **100% reutilizável:** O algoritmo TextTiling sobre janelas de sentenças (usando TF-IDF + similaridade de cosseno em NumPy ou modelo ONNX leve de 23 MB) roda em `< 0,2 s` em CPU e resolve o corte de assunto no meio (Decisão 5). |
| **OpusClip / Vizard** *(Comerciais)* | Pipeline em 4 estágios: (1) Transcrição timestamped; (2) Chunking semântico em blocos de 1–3 min; (3) Curadoria via LLM avaliando **Força do Gancho (*Hook*) nos primeiros 3–5 s**, **Fluxo Narrativo Autossuficiente** (se faz sentido fora de contexto) e **Desfecho (*Payoff*)**; (4) *Virality Score* composto (0–99). | **Reutilizável em arquitetura de 2 estágios:** Segmentar em arcos narrativos via regras + TextTiling e usar o LLM local leve (Decisão 1) para pontuar `hook`, `coerência` e `desfecho` apenas nos blocos pré-selecionados. |
| **Rhapsody** *(Park et al., COLM 2025 — `arXiv:2505.19429`)* | Benchmark de **13.000 episódios de podcast** usando o *"Most Replayed"* do YouTube como ground truth para treinar/avaliar detectores quando o heatmap não existe. Demonstra que **LLMs zero-shot apenas no texto cru falham**, mas **combinar features prosódicas da fala + transcrição** supera largamente modelos só de texto ou só de volume. | **Diretriz central para o projeto:** Não confiar apenas no LLM lendo texto nem só no dB; fundir **features prosódicas baratas** (ritmo de fala, pausas, modulação de voz, espectro de risada) com **features léxicas/estruturais** e nota do LLM. |
| **Mr. HiSum** *(Sul et al., NeurIPS 2023)* | Dataset de **31.892 vídeos do YouTube** com curvas *"Most Replayed"*. Mostra que os trechos mais reprisados coincidem com alta densidade informacional, mudanças de cadência e início de sub-tópicos autônomos. | **Reutilizável para calibração sem GPU:** Usar o próprio `heatmap` de VODs do YouTube como *silver standard* (ocultando-o na inferência) para calibrar os pesos do nosso detector offline. |
| **Literatura de Prosódia em Podcasts/Fala** *(Shriberg et al., 2000; Columbia Speech Group)* | Mostra que, na ausência de gritos, o engajamento na fala é marcado por **variação de frequência fundamental ($F_0$ / centroide espectral)**, **mudança na taxa de articulação (WPM / sílabas por segundo)**, **pausas dramáticas** antes/depois de frases-chave e **trocas rápidas de turno (turn-taking)**. | **100% reutilizável via STFT/NumPy:** Centroide espectral, fluxo espectral (risadas/exaltação mesmo com microfone comprimido!) e modulação da envoltória de fala rodam em `< 0,5 s` por hora de áudio em CPU. |

**Fontes verificadas (2026-11-17):**
- ClipsAI GitHub & Docs: `https://github.com/ClipsAI/clipsai` e `https://docs.clipsai.com/`
- Hearst, M. A. (1997), *TextTiling: Segmenting Text into Multi-paragraph Subtopic Passages*, Computational Linguistics: `https://aclanthology.org/J97-1003/`
- OpusClip ClipAnything / Virality Score Architecture: `https://www.opus.pro/clipanything`
- Park et al. (COLM 2025), *Rhapsody: A Dataset for Highlight Detection in Podcasts*: `https://arxiv.org/abs/2505.19429` e `https://github.com/younghanstark/rhapsody`
- Sul et al. (NeurIPS 2023), *Mr. HiSum: A Large-Scale Dataset for Video Highlight Detection and Summarization*: `https://openreview.net/forum?id=591124R09c` e `https://github.com/mr-hisum/mr-hisum`
- Shriberg et al. (2000), *Prosody-based automatic segmentation of speech into sentences and topics*, Speech Communication: `https://www.speech.sri.com/cgi-bin/run-distill?papers/spcom00-seg.ps.gz`

---

## 2. Abordagem "Segmentar Tudo + Pontuar Tudo" (Unidades de Assunto + Score Multi-Sinal)

Em vez de procurar segundos isolados de grito (`find_peaks`) e tentar encaixar uma janela cega de $\pm 45\text{ s}$ ao redor deles, invertemos a lógica para **Unidades Narrativas (Story Units)**:

### 2.1. Etapa 1 — Segmentação em Unidades de Assunto / Arco Narrativo

A partir de uma sequência de frases/orações com timestamps $\{(s_i, e_i, \text{texto}_i)\}_{i=1}^M$:

1. **Detecção de Fronteiras de Tópico (TextTiling Leve + Pausas + Marcadores Discursivos):**
   Para cada espaço entre a frase $i$ e a frase $i+1$, calculamos uma pontuação de fronteira de assunto $B_i$:
   - **Pausa acústica (`gap`):** $g_i = s_{i+1} - e_i$. Pausas longas ($g_i \ge 0,8\text{ s}$) após pontuação terminal (`.?!`) indicam fim natural de parágrafo oral.
   - **Vale de coesão léxica (*TextTiling cosine drop*):** Compara o vetor TF-IDF de char/word n-grams da janela de ~30 s antes de $i$ com a janela de ~30 s depois de $i+1$. Uma queda local de similaridade indica que os falantes mudaram de assunto.
   - **Marcadores de Abertura/Transição Narrativa (`HOOK_OPENERS_RE`):** Presença no início de $\text{texto}_{i+1}$ de expressões que introduzem histórias, polêmicas ou perguntas em pt-BR/EN:
     - *História/Relato:* `"teve uma vez"`, `"deixa eu te contar"`, `"sabe o que aconteceu"`, `"quando eu"`, `"eu lembro que"`
     - *Opinião Forte / Hot Take:* `"o problema é que"`, `"a verdade é"`, `"ninguém fala isso"`, `"vou ser sincero"`, `"é absurdo"`, `"discordo"`
     - *Pergunta Direta / Curiosidade:* `"por que você"`, `"qual foi a pior"`, `"como que"`, `"você acha que"`, `"quem que"`
2. **Construção de Arcos Candidatos (Janelas Semânticas de 30 s a 180 s):**
   - Agrupamos frases contíguas entre fronteiras fortes formando unidades de assunto de **30 s a 180 s** (permitindo assuntos longos de até ~3 min que depois serão fatiados em `Parte 1/N` de 45–60 s conforme a **Decisão 3**).

### 2.2. Etapa 2 — Pontuação Determinística Barata ($S_{\text{cheap}} \in [0, 1]$) em TODOS os Segmentos

Cada unidade narrativa candidata $u = [t_{\text{ini}}, t_{\text{fim}}]$ recebe um score rápido e 100% determinístico combinando **6 famílias de features** (sem custo de LLM):

$$S_{\text{cheap}}(u) = 0,25 \cdot F_{\text{hook}} + 0,20 \cdot F_{\text{arco}} + 0,20 \cdot F_{\text{prosodia}} + 0,15 \cdot F_{\text{dialogo}} + 0,20 \cdot F_{\text{reacao\_ext}}$$

1. **$F_{\text{hook}}$ (Força do Gancho nos primeiros 8 s do segmento):**
   - Presença de pergunta (`?`) nas 2 primeiras frases ($+0,40$);
   - Match em `HOOK_OPENERS_RE` ou `TITLE_KEYWORDS` de `render.py:13` ($+0,35$);
   - Frase inicial curta e incisiva de 4 a 12 palavras sem hesitação prolongada ($+0,25$).
2. **$F_{\text{arco}}$ (Estrutura Narrativa: Setup $\rightarrow$ Clímax/Punchline):**
   - Contraste entre a primeira metade e o terço final do segmento: presença de exclamações (`!`), palavras de intensidade emocional (`caralho`, `meu deus`, `mentira`, `absurdo`, `nunca`, `sempre`) ou risada/pausa de reação no terço final ($[60\%, 95\%]$ da duração).
3. **$F_{\text{prosodia}}$ (Prosódia Acústica sem depender de grito em dB):**
   - **Densidade e Ritmo de Fala (WPM):** Máximo quando a taxa de fala está na faixa dinâmica de **130 a 200 palavras/minuto** (penalizando trechos arrastados `< 70 WPM`).
   - **Modulação de Energia Voiced (`std_db`):** Desvio padrão do RMS dentro dos frames com voz ativa (diferencia fala expressiva/enfática de leitura monótona ou música de fundo constante).
   - **Fluxo Espectral de Risada/Exaltação (`spectral_laughter`):** Razão de energia na banda de `1500–4000 Hz` + modulação rítmica de `4–6 Hz` (captura risadas e gargalhadas na mesa do podcast mesmo quando o limiter do microfone impede que o volume suba em dB!).
4. **$F_{\text{dialogo}}$ (Dinâmica Pergunta-Resposta / Troca de Falante):**
   - Alternância de frases curtas seguidas de respostas ou mudança rápida de perfil espectral (centroide médio de voz entre frases separadas por micro-pausa de `0,15–0,6 s`), indicando debate vivo ou entrevista dinâmica.
5. **$F_{\text{reacao\_ext}}$ (Sinais Externos Disponíveis — Bônus Adaptativo):**
   - Se houver `chat` ou `heatmap`, usa o percentil do trecho. **Se não houver `chat` nem `heatmap` (live calma, VOD sem replay), os pesos de $F_{\text{reacao\_ext}}$ são redistribuídos automaticamente** para $F_{\text{hook}}$, $F_{\text{arco}}$ e $F_{\text{prosodia}}$ (exatamente como `signals.py:280` já faz para `BASE_WEIGHTS`, mas agora com sinais semânticos e prosódicos reais!).

### 2.3. Etapa 3 — Re-Ranking dos Top-$M$ com LLM Local Leve (0–10)

Para respeitar o piso de CPU/RAM, **não rodamos o LLM em centenas de janelas**.
- Ordenamos os segmentos por $S_{\text{cheap}}(u)$ (com diversificação temporal) e selecionamos apenas os **Top $M = \min(20, 3 \times \text{max\_clips})$ segmentos**.
- O LLM local leve (Decisão 1, ex.: `Qwen2.5-1.5B-Instruct` ou `3B` em Q4_K_M) recebe o texto transcrito de cada um dos $M$ segmentos e devolve notas de `0` a `10` para:
  - `hook` (0–10): a primeira frase prende a atenção sozinha?
  - `standalone` (0–10): o assunto tem começo, meio e fim compreensíveis sem contexto externo?
  - `impact` (0–10): há humor, revelação, opinião forte ou história interessante?
- **Score Final do Segmento:**
  $$S_{\text{final}}(u) = 0,45 \cdot S_{\text{cheap}}(u) + 0,55 \cdot \left(\frac{\text{LLM}_{\text{score}}(u)}{10,0}\right)$$

---

## 3. Custo de Transcrição em CPU (6 GB / 6 Threads): Viabilidade e Arquitetura em Duas Passadas

### 3.1. Conta Real no Hardware do Projeto (`docs/teste-real.md`)

Em `docs/teste-real.md` (linha 30), temos o benchmark exato medido no container Docker (`6g` RAM, `6 threads` do Ryzen 5 5500U):
- **Medição real:** `faster-whisper small` (`int8`, `beam_size=5` default) levou **`703,31 s` (11,72 min)** para transcrever **`24 min` (`1440 s`)** de áudio.
- **Real-Time Factor (RTF) real medido:**
  $$\text{RTF}_{\text{small, beam=5}} = \frac{703,31}{1440} = \mathbf{0,488\times}$$
- **Custo de transcrever um VOD de 3 horas (180 min) inteiro com `small (beam=5)`:**
  $$180\text{ min} \times 0,488 = \mathbf{87,9\text{ minutos (~1h28min) de CPU}}$$
  **Conclusão:** Transcrever 100% de um VOD de 3h diretamente com `faster-whisper small (beam=5)` é **inviável** para alto volume.

### 3.2. As 3 Soluções Combinadas que Reduzem o Tempo de 88 min para 6–15 min (em 3h de VOD)

#### Camada 1: Atalho Zero-CPU para VODs do YouTube (`auto-subs json3`)
Em VODs do YouTube (como o próprio Podpah testado), o YouTube já possui legendas automáticas com timestamps em nível de palavra (`json3` / `srv3`).
- Baixar a legenda automática via `yt-dlp --write-auto-subs --sub-langs "pt*,en*" --sub-format json3 --skip-download` leva **~2 segundos e 0% de CPU** para um vídeo de 3 horas!
- **Fluxo Híbrido (YouTube VOD):**
  1. Usa a legenda `json3` do YouTube + sinais acústicos de `audio.wav` para rodar o **Segmentador de Assuntos + $S_{\text{cheap}}$** no vídeo de 3 horas inteiro em **menos de 3 segundos**!
  2. Seleciona as Top $M$ janelas candidatas (~10 a 15 minutos totais de áudio).
  3. Roda o `faster-whisper small int8` **apenas nessas janelas candidatas** para obter pontuação (`.,?!`) e alinhamento precisos para o `.ass` e `snap.py`!

#### Camada 2: Duas Passadas de Áudio para Lives / Twitch / Vídeos Sem Legenda Pronta
Quando não há legenda prévia (ex.: gravações da Twitch, lives ativas ou VODs sem `auto-subs`):
1. **Passada 0 — Pré-Triagem Acústico-Prosódica 1 Hz no `audio.wav` inteiro (`< 1,5 s` de CPU por hora de vídeo):**
   - Extrai do `audio.wav` (16 kHz mono, lido em blocos via `wave` + `numpy` como em `compute_rms_db`):
     - **VAD de Energia Adaptativo + Taxa de Modulação Silábica (3–7 Hz):** Identifica onde há fala contínua e expressiva vs silêncio, música pura ou gameplay calado.
     - **Variação Prosódica e Espectral (1,5–4 kHz):** Captura mudanças de entonação, risadas e turnos de conversa mesmo sem pico de dB.
   - Seleciona os **Top 35% a 40% do vídeo** com maior atividade conversacional/prosódica em blocos de 45–90 s (~22 min de áudio por hora de vídeo).
2. **Passada 1 — ASR Rápido de Triagem (`faster-whisper base` ou `tiny`, `int8`, `beam_size=1`):**
   - Mudar de `small (244M params, beam_size=5)` para `base (74M params, int8, beam_size=1)` reduz o RTF no Ryzen 5 5500U (6 threads) de `0,488x` para **`~0,09x`** (~5,4x mais rápido, ocupando apenas ~350 MB de RAM!).
   - Transcrever os ~22 min de áudio pré-triados por hora de vídeo com `base int8 (beam_size=1)` leva apenas:
     $$22\text{ min} \times 0,09 = \mathbf{1,98\text{ min (~120 s) por hora de vídeo!}}$$
   - Sobre essa transcrição da Passada 1, rodamos a segmentação de assuntos + $S_{\text{cheap}}$ + LLM local nos top candidatos, escolhendo os **Top $K$ clipes finais** (~4 a 6 minutos de áudio útil por hora).
3. **Passada 2 — Refinamento Cirúrgico (`faster-whisper small int8`, `beam_size=2`):**
   - Roda `small int8` **somente nos ~5 minutos finais escolhidos** para gerar as timestamps perfeitas e pontuação rica para o `snap.py` e legendas `.ass`:
     $$5\text{ min} \times 0,32\text{ (small c/ beam=2)} = \mathbf{1,6\text{ min (~96 s) por hora de vídeo!}}$$

### 3.3. Orçamento Comparativo de Tempo por 1 Hora de Vídeo (6 Threads CPU)

| Estratégia | Tempo de CPU / 1h de Vídeo | Tempo p/ VOD de 3h | RAM de Pico | Cobertura Sem Picos |
|---|---:|---:|---:|---|
| *Baseline Inviável:* `small int8 (beam=5)` em 100% do áudio | `~29,3 min` | `~88,0 min` | `1,4 GB` | 100% |
| *Atual (`clipes-lives` hoje):* Só picos de dB/heatmap + `small (beam=5)` | `~3,9 min` | `~11,7 min` | `1,4 GB` | **Baixa/Zero em vídeo calmo** |
| **Proposta A (VOD c/ `auto-subs` YT + `small beam=2` nos Top-K)** | **`~2,0 min`** | **`~6,0 min`** | `1,4 GB` | **100% do vídeo analisado** |
| **Proposta B (Sem legenda: Pré-triagem Prosódica 38% $\rightarrow$ `base beam=1` $\rightarrow$ `small beam=2` nos Top-K)** | **`~4,2 min`** | **`~12,5 min`** | `1,4 GB` | **Top 38% de toda a fala analisada** |

---

## 4. Limiares Adaptativos, "Modo Piso" (Cota por Hora), Diversificação e Dedup

### 4.1. Limiares Adaptativos por Percentil Intravídeo

Em vez de subtrações fixas em dB (`db - baseline - 3.0` e `min_scale=8.0` em `signals.py:130,143`):
1. **Substituir o piso de escala fixo (`min_scale=8.0`) pelo IQR/MAD adaptativo da fala ativa:**
   - Em vídeos comprimidos onde $P_{99} - P_{50} = 2,2\text{ dB}$, usar `min_scale = max(1.5, 1.5 * IQR_voiced)` permite que variações prosódicas reais de $1,5\text{–}2,5\text{ dB}$ sejam mapeadas para $[0,4, 1,0]$ em vez de serem achatadas para $< 0,25$.
2. **Threshold de candidato relativo à distribuição do próprio vídeo:**
   - Um segmento é elegível se $S_{\text{final}}(u) \ge \max\left(S_{\text{floor}}, P_{75}(S_{\text{final}})\right)$, onde $S_{\text{floor}} = 0,35$.

### 4.2. "Modo Piso" (Quota Mode) Quando Não Há Picos

Para evitar que lives calmas ou podcasts sem chat/heatmap retornem `0` ou `1` clipe quando o usuário pediu `--max-clips 8`:
- Definimos uma **cota alvo por hora**: $Q_{\text{hora}} = \min\left(\text{max\_clips}, \max\left(2, \lceil 4 \times \text{horas\_de\_fala\_ativa} \rceil\right)\right)$.
- Se o detector de picos tradicional encontrar menos que $Q_{\text{hora}}$ candidatos com `raw_score >= 0.45`, o **Modo Piso** entra automaticamente em ação, preenchendo as vagas restantes com as melhores Unidades Narrativas ranqueadas por $S_{\text{final}}(u)$ (desde que o trecho tenha densidade mínima de fala $\ge 1,2\text{ palavras/s}$ e $S_{\text{final}} \ge 0,35$).
- O pipeline só entrega `0 clipes` se o vídeo realmente não contiver fala inteligível nem eventos acústicos (ex.: live de música instrumental lo-fi ou tela "Volto já", preservando o teste `test_zero_clips_flat_signal_degrades_cleanly`).

### 4.3. Diversificação ao Longo do Vídeo e Deduplicação

1. **Diversificação Temporal Estratificada + Penalidade Gaussiana:**
   - Um vídeo de duração $D$ é dividido em $B = \max(1, \min(\text{max\_clips}, \lfloor D / 900 \rfloor))$ blocos de 15 minutos.
   - Na primeira rodada de seleção gulosa, garante-se no máximo $\lceil \text{max\_clips} / B \rceil$ clipes por bloco de 15 min, aplicando penalidade de proximidade temporal após cada escolha $t_s$:
     $$S_{\text{ajustado}}(u) = S_{\text{final}}(u) \times \left(1 - 0,5 \cdot \exp\left(-\frac{(t_u - t_s)^2}{2 \cdot 180^2}\right)\right)$$
2. **Deduplicação Dupla (Temporal + Semântica):**
   - **Temporal:** Descarta qualquer candidato com sobreposição ( ou distância entre bordas `< 15 s`) com um clipe já selecionado (se ambos pertencerem ao mesmo assunto longo de até 3 min, eles são fundidos e fatiados em `Parte 1/N`!).
   - **Léxica (Jaccard de 3-gramas de palavras):** Se dois candidatos em momentos diferentes do vídeo tiverem similaridade de Jaccard $\ge 0,45$ nos 3-gramas de palavras (ex.: o streamer repetiu a mesma piada ou leu o mesmo donate duas vezes), mantém-se apenas o de maior score.

---

## 5. Como Calibrar Sem GPU (Conjunto Rotulado Pequeno + `Precision@K` + Ablação de Heatmap)

Não é necessário GPU nem milhares de horas rotuladas manualmente para calibrar os pesos de $S_{\text{cheap}}$ e do prompt do LLM local:

### 5.1. O Truque da "Ablação de Heatmap" (Auto-Supervisão Gratuita estilo *Rhapsody* / *Mr. HiSum*)
1. Pegamos **5 a 10 VODs do YouTube** que possuam `heatmap` (*"Most Replayed"*) — começando pelo próprio VOD `UBv07VGeD24` (Podpah `#1049`, 2h41min) já cacheado em `/home/romulo/Downloads/clipes-lives/UBv07VGeD24/`.
2. Definimos como **Ground Truth Prata ($G$)** os intervalos de 60 s centrados nos Top-10 picos do `heatmap` real do YouTube (mais os 8 clipes validados em `docs/teste-real.md` + 2 da live `cStJTQf9XUk`, e 20–30 trechos negativos amostrados de partes onde `heatmap < 0.15`).
3. **Escondemos o `heatmap` e o `chat`** do nosso detector (`heatmap=None, chat_messages=None`), forçando-o a escolher os Top-$K$ ($K=8$) momentos **usando apenas áudio + transcrição (Prosódia + TextTiling + $S_{\text{cheap}}$)**.
4. Medimos automaticamente em CPU (leva `< 2 s` usando o cache!):
   - $\text{Precision@}K = \frac{1}{K} \sum_{i=1}^K \mathbb{I}\left(\exists g \in G : \text{IoU}(\text{clip}_i, g) \ge 0,30\right)$
   - $\text{Recall@}K$ (já implementado em `clipes/eval.py:69-78` via `ground_truth_peaks`!).
5. Ajustamos os 5 pesos de $S_{\text{cheap}}$ via busca em grade simples (*grid search* de 100 combinações em NumPy, que roda em `< 0,5 s`!) maximizando $\text{Recall@8} + \text{SentenceEdgeRate}$.

---

## 6. Desenho Concreto e Pequeno: Fórmulas, Mudanças no Código e Ordem de Implementação

### 6.1. Pipeline Proposto (Visão Geral Enxuta)

```
[Ingest: audio.wav + (auto-subs json3 se YT VOD)]
       │
       ▼
[1. Sinais 1 Hz (signals.py)]
   ├─ signal_loudness (com IQR adaptativo p/ voz comprimida)
   ├─ signal_prosody  (NOVO: modulação de voz + fluxo espectral 1.5-4kHz + pausas)
   └─ signal_chat / signal_heatmap (opcionais, quando existirem)
       │
       ▼
[2. Geração de Janelas Candidatas (candidates.py)]
   ├─ Picos multimodais clássicos (find_peaks)
   └─ + Modo Piso / Regiões Prosódicas (preenche cota p/ cada bloco de 15 min)
       │
       ▼
[3. Transcrição em 2 Níveis (asr.py)]
   ├─ Se já tem auto-subs ou cache: usa p/ pré-pontuar
   ├─ Passada rápida (base int8, beam=1) nas regiões candidatas amplas
   └─ Segmentação de Assunto (TextTiling + pausas >=0.8s + HOOK_OPENERS_RE)
       │
       ▼
[4. Pontuação + Snap Narrativo / Partes 1/N (snap.py)]
   ├─ Pontua cada unidade narrativa: S_cheap (Hook + Arco + Prosódia + Diálogo)
   ├─ (Opcional) Re-rank dos Top-16 com LLM local Q4_K_M (nota 0-10)
   ├─ Refina ASR (small int8, beam=2) só nos Top-K aprovados
   └─ Snap sem obrigar peak central (permite hook no início / punchline no fim)
      e divide assuntos de 60-180s em Parte 1/N de 45-60s nas bordas de frase!
```

### 6.2. O Que Muda em Cada Arquivo de `clipes/`

1. **`clipes/signals.py` (~60 linhas adicionadas):**
   - Em `signal_loudness`: trocar `min_scale=8.0` fixo por escala adaptativa `min_scale = max(2.0, float(np.percentile(voiced, 90) - np.percentile(voiced, 50)))` e reduzir deadband de `3.0 dB` para `1.5 dB` quando o range dinâmico do vídeo for pequeno (microfone com compressor).
   - Adicionar `signal_prosody(audio_path, rms_db, n) -> np.ndarray`: calcula a cada 1 s a variância móvel da fala ativa em janelas de 8 s + densidade de transições fala/pausa (`syllable/turn modulation`), entrando em `BASE_WEIGHTS` (`loudness: 0.25, prosody: 0.25, chat: 0.30, heatmap: 0.20`).
2. **`clipes/candidates.py` (~50 linhas adicionadas):**
   - Adicionar **Modo Piso e Estratificação Temporal** em `pick_candidate_peaks`:
     - Se `find_peaks` retornar menos candidatos fortes do que a cota ou concentrar tudo em um único trecho, divide os `n` segundos em blocos de 15 min e seleciona as janelas de 60 s com maior média de `prosody + loudness` em cada bloco, garantindo sempre $2 \times \text{max\_clips}$ janelas diversificadas para o ASR.
3. **`clipes/asr.py` (~35 linhas modificadas):**
   - Adicionar parâmetro `beam_size: int = 2` (reduzindo o custo de CPU em ~40% vs default `5`).
   - Suportar transcrição em 2 níveis: `base` (`beam_size=1`) para triagem de janelas largas quando houver muitas janelas sem chat/heatmap, e `small` (`beam_size=2`) nos candidatos selecionados.
4. **`clipes/snap.py` (~90 linhas modificadas/adicionadas):**
   - Criar `score_narrative_segment(words_slice, rms_slice) -> float`: calcula $F_{\text{hook}}$ (`?`, `HOOK_OPENERS_RE`, `TITLE_KEYWORDS` nos primeiros 8 s), $F_{\text{arco}}$ (exclamações/reações + queda de energia pós-punchline) e WPM (`130–200 WPM`).
   - **Relaxar a restrição `PEAK_POS_MIN (0.35) .. PEAK_POS_MAX (0.75)` para segmentos narrativos:** quando `edge_mode == "speech"` e `s_type == "sentence"` e `e_type == "sentence"`, aceitar qualquer posição de pico em `[0.10, 0.92]` desde que $S_{\text{cheap}}$ do arco seja alto! Isso permite capturar histórias onde o gancho está no começo ou a piada termina no final.
   - **Suporte a Assuntos Longos (`split_into_parts` — Decisão 3 e 5):** Se o arco narrativo coeso entre duas pausas longas/mudanças de tópico durar entre `60s` e `180s`, em vez de descartá-lo por exceder `max_dur=55s`, dividi-lo em 2 ou 3 partes de `45–60s` (`Parte 1/N`), cortando exatamente na borda de frase (`.?!`) de maior tensão entre `45s` e `60s`.
5. **`clipes/cli.py` (~15 linhas):**
   - Expor flag opcional `--quota-per-hour` (padrão ativo no modo piso) e registrar no `clips.json` o `narrative_score` ao lado do `signal_scores`.

### 6.3. Ordem Recomendada de Implementação

1. **Passo 1 (Impacto Imediato, Zero Dependência Nova):** Implementar `signal_prosody` + escala adaptativa em `signals.py` e o **Modo Piso estratificado** em `candidates.py`. *(Resolve imediatamente o problema de vídeos calmos/sem heatmap gerarem 0 candidatos).*
2. **Passo 2 (Qualidade de Corte e Narrativa):** Adicionar `score_narrative_segment` (`F_hook` + `F_arco` + WPM), relaxar `PEAK_POS` para bordas de frase com gancho e implementar a divisão de assuntos de até 3 min em `Parte 1/N` (45–60 s) em `snap.py`.
3. **Passo 3 (Velocidade de CPU):** Ajustar `beam_size=2` e implementar a triagem em 2 passadas (`auto-subs` em VODs YT / `base int8 beam=1` -> `small int8 beam=2`) em `asr.py` e `ingest.py`.
4. **Passo 4 (Calibração e LLM Local):** Rodar o script de calibração por ablação de heatmap no VOD `UBv07VGeD24` (`Recall@8` em `eval.py`) e plugar o re-ranking dos Top-$M$ segmentos com o LLM local leve (Pesquisa A).
