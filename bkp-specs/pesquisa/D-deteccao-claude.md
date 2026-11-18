# Pesquisa D (extra): detectar bons momentos sem depender de picos

Acesso às fontes: **2026-11-17**. Rótulos: **[medido]** (rodei em Docker), **[fonte]** (URL), **[estimativa]**, **nao verificado**. Código lido: `clipes/candidates.py`, `signals.py` (assinaturas), `snap.py`, `cli.py`, `asr.py`, `docs/teste-real.md`.

## 0. Diagnóstico no código

- `candidates.py::pick_candidate_peaks` já usa limiar **relativo** (`mediana + 8% de (máx - mediana)`), `find_peaks` com `distance=min_separation(90 s)` e `limit = max_clips*2`. Problema real: tudo parte de **uma curva fundida** (loudness + chat + heatmap); sem chat/heatmap sobra só loudness, e em podcast conversado a curva é achatada → poucos picos com `prominence≥0.01`, ou picos que são só o locutor levantando a voz.
- O ASR hoje roda **só nas janelas de 90 s dos picos** (`asr.py::transcribe_candidates`, 16 janelas = 24 min de áudio, 703 s no teste real = 0,49 xRT com beam 5 + word timestamps). Não existe visão do texto do vídeo todo, então não dá para achar "história com punchline" sem pico.
- `max_clips` limita a saída e `limit = max_clips*2` limita os candidatos: o volume depende dos picos, não de uma cota.

## 1. Como outros escolhem momentos sem pico de áudio

| Abordagem | O que faz | Reutilizável em CPU/6 GB? | Fonte |
|---|---|---|---|
| Pontuação por LLM de segmentos de transcrição | cada trecho recebe nota (ex.: 1-100) por relevância/gancho; a série vira clipes | **Sim**, com LLM pequeno (custo alto por volume; ver §3) | Sieve (descrição na busca; a página deu 404 ao buscar, então só o trecho da busca): https://sievedata.com/blog/generate-video-highlights-long-form-content-podcasts |
| Score "viralidade" comercial (OpusClip): hook, fluxo, valor, trend; usa transcrição + dinâmica de áudio (variação de tom, ritmo, risada) + visual | caixa-preta, modelo treinado em dados proprietários (afirmação do vendor, não verificável) | Só as **ideias de features** (gancho no início, arco, risada, ritmo) | https://www.opus.pro/blog/how-ai-video-clipping-reveals-hidden-viral-moments |
| Rhapsody (COLM 2025): 13 mil podcasts com "most replayed" como rótulo; baselines | ver abaixo | Resultado de método, não o dataset (CC BY-NC-SA, só pesquisa; só IDs/features) | https://arxiv.org/abs/2505.19429 |
| ClipsAI e demais open source | **nao verificado** (a busca não retornou internals; não li o repo) | n/a | n/a |

**Achado mais útil (Rhapsody, Tabela de resultados, 2.467 episódios de teste):** hit-rate / AP — aleatório 0,220/0,021; heurística de frequência 0,224/0,031; BM25 0,216/0,024; **GPT-4o zero-shot 0,265/0,032**; Gemini texto 0,327/0,037; Llama-1B **fine-tunado** 0,452/0,134; + HuBERT áudio 0,490/0,153. Fonte: https://arxiv.org/html/2505.19429v2 . **Leitura:** LLM zero-shot em texto quase não bate o acaso para prever o que a audiência reouve (e mesmo o melhor chega a ~49% de acerto). Logo: **não confiar em nota de LLM pequeno como critério principal**. Usá-lo como filtro/desempate junto de features baratas e, principalmente, como **gerador de título/legenda**. (Ressalva: rótulos "most replayed" ≠ "bom clipe para Shorts"; extrapolação para pt-BR/live: nao verificado.)

## 2. "Segmentar tudo + pontuar tudo" (modo cota)

### 2.1 Unidades
1. ASR do vídeo (ou das zonas triadas, §3) → palavras com tempo.
2. Frases = corta em `Word.ends_sentence` ou pausa ≥ 0,5 s (já existe a lógica em `snap.py`).
3. Segmentos de assunto = TextTiling léxico sobre janelas de ~8 frases (cosseno de BoW sem stopwords pt; fronteira onde a similaridade cai abaixo de `média - 0,5·desvio`). Tamanho alvo 25-180 s (decisão do usuário: até ~3 min; dividir em partes de 45-60 s é outra frente). Sem modelo; embeddings só se falhar (ver C).
4. Arco narrativo (setup→punchline) sem modelo: aproximar por "pergunta seguida de resposta curta", "risada/`kkk` no fim", "frase curta e alta depois de pausa".

### 2.2 Features baratas por segmento (todas normalizadas por **percentil dentro do próprio vídeo**, 0-1)
| Feature | Como | Custo |
|---|---|---|
| `loud` | pico de RMS dB do segmento menos mediana local (já em `signals.py`) + desvio-padrão (dinâmica) | 0 |
| `chat` | `signal_chat` relativo ao baseline do canal (já existe), quando houver | 0 |
| `heat` | heatmap do YouTube, quando houver | 0 |
| `exclam` | nº de `!` e `?` por 100 palavras | 0 |
| `laugh` | tokens `kkk+`, `haha`, `rs`, "(risos)" na transcrição | 0 |
| `qa` | pergunta (`?`) seguida de resposta em ≤ 6 s | 0 |
| `rate` | palavras/s em z-score (aceleração = emoção) | 0 |
| `kw` | lista curta de palavras de reação/história ("cara", "não acredito", "olha só", "então eu…") — **lista a calibrar**, nao verificado que ajude | 0 |
| `speaker_change` | **sem diarização não há como**; proxy: pausa curta + mudança brusca de nível RMS. Nao validado. | 0 |
| `llm` | nota 0-10 do LLM leve (Qwen3-1.7B) sobre o texto do segmento | ~5-10 s/segmento [estimativa, nao medido] |

### 2.3 Fórmula
```
base  = 0.30*loud + 0.15*exclam + 0.15*laugh + 0.10*qa + 0.10*rate + 0.10*chat_ou_heat + 0.10*kw   # pesos iniciais: a calibrar (§5)
score = base                         # fase 1, todos os segmentos
score = 0.7*base + 0.3*(llm/10)      # fase 2, só o top 3K por `base` (poupa LLM)
```
O LLM só vê o top da fase 1 (≈3× a cota): coerente com o resultado do Rhapsody (LLM fraco sozinho).

### 2.4 Seleção (cota)
```
horas = duração/3600
K = max(MIN_PER_HOUR*horas, nº de picos fortes)  limitado por max_clips
candidatos = segmentos ordenados por score
piso de qualidade: score >= percentil 80 do vídeo  E  (llm >= 5 se houver llm)
diversificação: espaçamento mínimo 300 s (ou MMR: score - 0.5*max_sim_com_já_escolhidos)
dedup: cosseno TF-IDF entre textos > 0,6 → mantém o de maior score
```
Sugestão de `MIN_PER_HOUR` = 3 (**valor de projeto, a ajustar**; não vem de fonte). Se o piso de qualidade deixar < N, **emitir menos e dizer no relatório** (melhor que lixo); parâmetro `--quota-relax` baixa o percentil para 70.

## 3. Custo de transcrever tudo (VOD de 3 h)

Medido nesta pesquisa (container 6 GB/6 CPUs, pt-BR, int8): small **0,29 xRT** (beam 1, sem word ts) [medido]; projeto atual 0,49 xRT (beam 5 + word ts) [medido no teste real]. large-v3-turbo 0,38 xRT [medido]. Hardware de teste ≠ Ryzen 5500U (ver C).

| Estratégia | 3 h de VOD (10.800 s) | Verdict |
|---|---:|---|
| ASR total, small, config atual (0,49) | ~88 min | inviável em volume |
| ASR total, small, beam 1 sem word ts (0,29) | ~52 min | aceitável só como opção "completo" |
| ASR total, turbo (0,38) | ~68 min | idem, melhor texto |
| **Duas passadas** (recomendado) | ver abaixo | **sim** |

**Duas passadas:**
- Passada 0 (~segundos, sem ASR): VAD (o `faster-whisper` já traz Silero VAD; custo ~ms/min [estimativa]) + RMS 1 Hz → por bloco de 30 s: `speech_frac`, `loud_pct`, `loud_std`, `chat/heat` quando houver. Descarta blocos com `speech_frac < 0,3` (música/silêncio/jogo sem fala).
- Passada 1: ASR **beam 1, sem word timestamps** só nos blocos que passam, até um **orçamento** `T_ASR` (padrão 25 min de parede ≈ 52 min de áudio com small a 0,29 xRT; ≈ 30 min de áudio a 0,49). Ordem: blocos por `pre_score` decrescente, **estratificado por terço do vídeo** (para diversificar). Isso cobre ~30-50% de um VOD de 3 h.
- Passada 2: re-ASR **com word timestamps** apenas nas janelas dos clipes escolhidos (~8-12 clipes × ~70 s ≈ 14 min de áudio ≈ 7 min), que é o que o `snap` precisa.
- Paralelismo: 2 processos × 3 threads não ganham em CPU-bound com 6 núcleos; mantém 1 × 6 (**nao medido**).
- Orçamento de tempo por hora de vídeo: passada 0 ≈ 1-2 s; passada 1 ≈ 8 min (≈ 17 min de áudio a 0,49 / 29 a 0,29); passada 2 ≈ 3-7 min; LLM ≈ 3-5 min; render ≈ 8 min → **~25-30 min de parede por hora de vídeo** [estimativa derivada das medições].
- RAM: Whisper ≈ 1,6-2 GB; LLM 3-3,5 GB [estimativa]; **sequenciais**, pico ≈ 3,5 GB (<6 GB).
- Twitch/live sem replay: passada 0 sobre o buffer gravado + chat relativo; mesma lógica.

## 4. Limiares adaptativos e piso

- **Tudo em percentil por vídeo**, nunca absoluto: `robust_percentile_norm` já existe em `signals.py` (reuso).
- **Modo piso:** garante `N_min = ceil(MIN_PER_HOUR*horas)` apenas se existirem segmentos acima do piso de qualidade; senão emite menos, avisa.
- **Diversificação:** espaçamento ≥ 300 s + quota por terço do vídeo (no máximo 50% dos clipes no mesmo terço).
- **Dedup entre candidatos:** TF-IDF cosseno > 0,6 ou interseção temporal > 30%; vale também entre picos (modo atual) e segmentos (modo cota).
- Picos fortes (modo atual) continuam entrando: `score_pico` é só mais um sinal na fusão; um clipe pode vir de pico OU de segmento bem pontuado.

## 5. Calibração sem GPU

1. **Conjunto rotulado pequeno:** 30-50 momentos (15-25 bons, 15-25 ruins) de 3-4 vídeos de tipos diferentes (podcast, live de jogo, Twitch). O Rômulo marca "bom/ruim/talvez"; o repo guarda só um JSON `{video_id, t_start, t_end, label}` (sem mídia, sem segredos).
2. **Métrica:** precision@K (K = cota) e recall@K sobre os "bons"; relatar também fração de clipes que o usuário "publicaria sem editar".
3. **Aproveitar o que já existe:** os 8 clipes de `docs/teste-real.md` (clip_01…08, UBv07VGeD24) como **positivos** — com ressalva de **viés circular** (foram escolhidos por pico, então favorecem `loud`); negativos: janelas aleatórias do mesmo vídeo com mesma duração; e os 8 candidatos descartados de 16 do teste.
4. **Ajuste:** grid search nos pesos (poucas dimensões, 10 features) maximizando precision@K por validação cruzada entre vídeos (leave-one-video-out). Sem treino de modelo.
5. **Alerta do Rhapsody:** não esperar que o LLM acerte sozinho; medir `llm` como feature extra e manter só se subir precision@K.

## 6. Desenho concreto (pequeno) e ordem de implementação

Arquivos/funções (nomes propostos):

| Mudança | Onde | Conteúdo |
|---|---|---|
| 1 | `clipes/triage.py` (novo) | `block_features(bundle, audio, vad)->list[Block]`; `select_blocks(blocks, budget_s)` (estratificado) |
| 2 | `clipes/asr.py` | `transcribe_blocks(blocks, fast=True)` = beam 1 sem word ts (reusa `merge_windows`/`load_audio_slice`); `transcribe_candidates` continua como passada 2 (com word ts) |
| 3 | `clipes/segment.py` (novo) | `sentences(words)`, `texttile(sentences)`, `segment_features(seg, bundle)` |
| 4 | `clipes/candidates.py` | `pick_quota_candidates(segments, hours, min_per_hour, pct_floor, min_sep)` retornando `CandidatePeak` (peak = centro do segmento; `start_win/end_win` = limites do segmento) → **`snap.py` não muda** |
| 5 | `clipes/cli.py` | `--mode {peaks,quota,auto}`; `auto` = picos, e se saírem < N_min completa com cota; `--min-per-hour`, `--asr-budget-min` |
| 6 | `clipes/score_llm.py` | opcional, só depois de 1-5 (interface `complete(prompt)->str`; modelo via llama.cpp) |

Ordem: **(1) triagem + (2) ASR rápido** → **(3) segmentação + features** → **(4) cota/dedup/diversificação** → **(5) CLI/`auto`** → medir precision@K com o conjunto de §5 → **(6) LLM** só se ajudar. A fusão atual de sinais fica intacta (entra como features `loud/chat/heat`).

Pontos em aberto: pesos iniciais são chutes de projeto (não derivam de fonte); lista de palavras-chave `kw` e o proxy de troca de falante **nao validados**; qualidade do Qwen3-1.7B em pt-BR **nao medida** (ver C); ClipsAI/outros open source **nao verificados**.
