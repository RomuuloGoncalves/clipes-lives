# Pesquisa D: detectar bons momentos sem depender de picos

Data da pesquisa: 2026-11-17. Autor: pesquisador D (Sonnet). Somente leitura de código + web. Fato sem URL = **não verificado**.

## 0. Diagnóstico do código atual (verificado lendo o repo)

- `clipes/signals.py::compute_signals` funde loudness + chat + heatmap numa curva 1 Hz (`smoothed`). Cada sinal é normalizado por percentil 50→99 (`robust_percentile_norm`), então já é *relativo ao vídeo*, mas o resultado é sempre "o que sobressai", mesmo num vídeo sem nada.
- `clipes/candidates.py::pick_candidate_peaks` usa `find_peaks` com `height = mediana + 0.08*(max - mediana)`, `distance=90`, `prominence=0.01`, e corta em `max_clips*2`. Consequência: **o nº de candidatos é limitado pelo nº de picos**, não pela cota. Curva plana (`max <= 0` ou nenhum pico) → `[]` → `cli.py` grava manifesto vazio e sai com 0 (`cli.py:205-226`).
- Em live: só loudness (sem heatmap, sem chat replay). Em VOD YouTube: loudness + heatmap. Twitch VOD: loudness + chat (se houver replay).
- `asr.py::transcribe_candidates`: ASR só nas janelas candidatas (`start_win/end_win` = pico ± 45 s), `small` int8, 6 threads, `vad_filter=True`.
- `snap.py::select_clips` recebe candidatos + palavras e ajusta bordas (não decide *o que* é bom).
- Medição real (`docs/teste-real.md`): 9659 s de vídeo, 16 janelas de 90 s = 1444 s de áudio transcrito em 703 s → **RTF ≈ 0,49**. Live de 4 min: 2 janelas em 81 s.
- Nada no código pontua conteúdo (texto). `generate_title` é heurística de `!`/`?`.

## 1. Como outras ferramentas escolhem momentos sem depender de pico de áudio

| Ferramenta / trabalho | Método (o que a fonte diz) | Fonte | Reutilizável em CPU/6 GB? |
|---|---|---|---|
| **ClipsAI** (open source) | Usa **TextTiling** (Hearst, anos 90) sobre a transcrição para dividir em trechos coerentes por mudança de tópico; variante com embeddings BERT; transcrição via WhisperX. A doc não explica como *ranqueia* os trechos (não verificado: heurística de seleção; ver código no GitHub). | https://clipsai.com/references/clip (acessado 2026-11-17) | **Sim** a ideia (segmentação por coesão lexical, sem GPU). BERT é opcional e pesado; TF-IDF/cosseno basta. |
| **OpusClip** (comercial) | "Virality score" 0–99; fator "Hook" confirmado na doc oficial. Lista completa de fatores truncada (não verificado). Blog/review de terceiros fala de emoção, retenção, tendências (secundário, não verificado). ClipAnything "usa pistas visuais, de áudio e de sentimento" e dá nota por cena (não verificado em página primária). | https://help.opus.pro/docs/article/virality-score (2026-11-17) | Fechado; só a *ideia*: segmentar tudo → nota por segmento → ordenar. |
| **Vizard e similares** | Não pesquisado a fundo; **não verificado**. | — | — |
| **Rhapsody** (paper, COLM 2025) | Dataset ~13K podcasts; tarefa = classificar segmentos (transcrição dividida em 100 partes). Ground truth = curva "most replayed" do YouTube. Llama-3.2-1B fine-tunado (QLoRA) supera GPT-4o/Gemini zero-shot por boa margem; a tarefa é difícil mesmo para modelos de ponta. | https://arxiv.org/abs/2505.19429 (2026-11-17) | **Insight forte:** (a) LLM zero-shot por segmento é fraco → não confiar só nele; (b) o heatmap do YouTube serve de **rótulo grátis** para calibrar nossos scores em VODs que o tenham. O fine-tune em si não é viável sem GPU. |
| **Sieve (blog)** | Pontuar cada segmento 1–100 individualmente perdeu bons highlights; passaram a dar ao LLM um lote ordenado de segmentos e pedir que escolha. | https://sievedata.com/blog/generate-video-highlights-long-form-content-podcasts (2026-11-17) | Sim: **comparar segmentos entre si** (rank dentro do vídeo) em vez de nota absoluta. Casa com limiar por percentil. |

Conclusão da seção: o padrão comum é **"segmentar tudo → pontuar cada segmento → ranquear"**, não "achar picos". Picos de áudio são só *uma feature*. Para CPU/6 GB: reutilizável = segmentação lexical (TextTiling-like), features baratas, ranking relativo; não reutilizável = fine-tune/LLM grande/visão multimodal.

## 2. "Segmentar tudo + pontuar tudo"

### 2.1 Unidade de candidato
Dois estágios, porque a transcrição ainda não existe no estágio barato:

1. **Estágio A (sem ASR): janelas deslizantes** de 60 s, passo 20 s, cobrindo o vídeo inteiro (3 h → 540 janelas). Pontuadas só por sinais de áudio/chat/heatmap (seção 2.2).
2. **Estágio B (com ASR nas janelas aprovadas): refinar em unidades de assunto.** TextTiling simplificado nas palavras: blocos de ~20 s, coesão = cosseno TF-IDF entre blocos vizinhos; fronteira onde a coesão cai abaixo de `média - 0.5*desvio`. Une até ~3 min (decisão 3 do usuário), corta em partes de 45–60 s depois (já é problema da outra pesquisa/snap).

### 2.2 Features baratas (todas por janela W)

Todas já normalizadas por **percentil dentro do próprio vídeo** (`rank_pct`, 0–1), nunca absolutas.

| Feature | Como calcular (CPU barato) | Existe hoje? |
|---|---|---|
| `loud` | média de `signal_loudness` na janela e máx. | sim |
| `chat` | idem `signal_chat` | sim (só com replay) |
| `heat` | idem `signal_heatmap` | sim (só VOD YT) |
| `speech_density` | fração de segundos com `rms_db > SILENCE_DB` (e/ou VAD Silero) | parcial (silêncio mascarado) |
| `rate_proxy` | nº de "ataques" na envoltória de energia por segundo (proxy de ritmo de fala, sem ASR) | novo, ~10 linhas numpy |
| `dyn` | desvio-padrão do `rms_db` na janela (conversa animada vs. monótona) | novo |
| `overlap/turn` | alternância rápida de energia + pausas curtas (proxy de troca de falante; diarização real = pesada, **não recomendada**) | novo, opcional |

Features de texto (só pós-ASR, estágio B):

| Feature | Cálculo |
|---|---|
| `laugh` | regex em transcrição ("kkk", "haha", `[risos]`) + risada de chat; Whisper nem sempre transcreve risada: **não verificado** como confiável |
| `excl_q` | `(#! + #?) / palavras` |
| `qa` | pergunta seguida de resposta curta em < 3 s (padrão "pergunta→punchline") |
| `kw` | lista de palavras (pt-BR/EN: "meu deus", "não acredito", "mano", "história", "sério", "caralho"…) |
| `hook` | 1ª frase do segmento é pergunta/afirmação forte (OpusClip cita "hook" como fator; ver tabela) |
| `llm` | nota 0–10 de um LLM local leve, **só nos top-M segmentos** (ver 2.4) |

### 2.3 Pontuação (fórmula)

```
S_A(w) = 0.30*loud + 0.20*dyn + 0.15*rate_proxy + 0.10*speech_density
         + [0.25*chat se houver] + [0.25*heat se houver]     # renormalizar pesos pelos sinais ativos
S_A bonus de concordância: ×(1 + 0.15*max(0, n_sinais_acima_do_p75 - 1))   # mesmo padrão de signals.py

S_B(seg) = 0.50*S_A + 0.20*text_cheap + 0.30*llm/10          # se llm disponível
text_cheap = 0.3*laugh + 0.25*excl_q + 0.2*qa + 0.15*kw + 0.1*hook   # cada um em rank_pct
```
Pesos são **chute inicial, não verificado**; calibrar na seção 5.

### 2.4 LLM local leve
- Entrada: transcrição do segmento (≤ ~300 palavras), prompt curto, saída JSON `{nota:0-10, motivo}`.
- Só roda em top-M (M ≈ 3×cota) após o ASR, sequencial, depois de descarregar o Whisper (regra do piso).
- Evidência: Rhapsody e Sieve mostram que nota absoluta por segmento é instável → **usar a nota como 30% do score e comparar entre segmentos**, nunca como única decisão.
- Modelo concreto (Qwen/Llama 1–3B quantizado Q4 via llama.cpp, ~1–2 GB RAM) cabe no piso, mas **a escolha e a velocidade em Ryzen 5500U não foram verificadas aqui**; a decisão 1 do usuário (LLM local, pluggável) é compatível. Medir antes de fixar.

### 2.5 Seleção top-K por hora com piso (modo cota)

```
K = clamp(round(quota_por_hora * duracao_h), 1, max_clips)      # ex.: quota 4/h
piso_qualidade = percentil_P (p.ex. p60) de S_B no vídeo  E  S_B >= S_min_abs (anti-lixo: p.ex. 0.15)
ordenar S por desc; escolher guloso com:
  - NMS: descarta se |centro - centro_escolhido| < min_sep   (min_sep = 120 s)
  - diversificação: no máx. ceil(K/3) clipes por terço do vídeo, 1º passe; depois completa livre
```
Se menos de K passam do piso → entrega só os que passam (e registra no manifesto `note`), **exceto** no modo "piso" (seção 4), que relaxa o percentil.

## 3. Custo de transcrever o vídeo inteiro em CPU

Medido no repo: RTF `small` int8 ≈ 0,49 (703 s / 1444 s). Projeção (conta própria, aritmética, não medida):

| Vídeo | ASR inteiro `small` | Observação |
|---|---|---|
| 1 h | ≈ 29 min | |
| 3 h | ≈ 88 min | inviável como padrão para alto volume |

Referência externa para modelos menores (arXiv, i7-1370P, faster-whisper 1.2.1 int8, LibriSpeech, inglês): tiny.en RTF 0,154; base.en 0,283; small.en 0,520 (https://arxiv.org/pdf/2607.28878, 2026-11-17). Batem com nosso 0,49 para `small`. Ressalvas: fala limpa em inglês, CPU mais rápida que o Ryzen 5500U; **tiny/base em pt-BR e áudio de live seriam piores em WER: não verificado**. Projeção grosseira para tiny: 3 h ≈ 28 min (sem VAD), mas com qualidade baixa demais para legenda; serve só como triagem.

Alternativas, em ordem de custo-benefício:

1. **Duas passadas (recomendado).** Passada 1 = sinais baratos (seção 2.2) em todo o vídeo: ~0,6 s para 9659 s (medido, `signals_s`). Passada 2 = ASR `small` só nos top ~(2–3)×K janelas de 60–90 s. Orçamento por hora de vídeo: com quota 4/h e fator 3 → 12 janelas × 75 s = 15 min de áudio ≈ **7,5 min de ASR por hora de vídeo** (conta própria, RTF 0,49). 3 h ≈ 22 min.
2. **Pré-triagem com ASR tiny** (RTF ≈ 0,15 na fonte acima) em todo o vídeo só para obter texto bruto/taxa de fala, depois `small` só nos aprovados. Custa ~28 min/3 h; só vale se features de áudio forem insuficientes. **Não recomendado como 1º passo.**
3. **VAD para pular silêncio:** faster-whisper já integra Silero com `vad_filter=True` (já usado em `asr.py`); notas de versão dizem VAD 3× mais rápido na CPU na 1.1.0 (fonte secundária via busca; ganho real depende do silêncio, não medido). Em live conversada o ganho é pequeno.
4. **Paralelismo:** 6 threads já em uso; dividir em 2 processos × 3 threads normalmente não dá ganho líquido e dobra RAM do modelo: **não verificado**, medir antes.
5. **`distil-whisper`/`turbo`:** não pesquisado aqui; `large-v3-turbo` já medido no repo como ~2× mais lento que `small` (docs/teste-real.md).

Regra: tempo de ASR por hora de vídeo = `K_janelas × dur_janela × RTF`; expor `--asr-budget-min` e parar de adicionar janelas ao estourar (ordem = S_A desc).

## 4. Limiares adaptativos, modo piso, diversificação, dedup

- **Adaptativo:** trocar `threshold = floor + 0.08*(max-floor)` (depende do máximo: um único pico enorme "esmaga" o resto) por **percentil do vídeo**: candidatos = janelas com `S_A >= p(100 - 100*N_cand/n_janelas)`. Ex.: top 15% das janelas.
- **Cota, não pico:** `find_peaks` deixa de limitar; vira apenas bônus de feature (`is_peak`), não gate.
- **Modo piso:** se após o piso de qualidade `len(sel) < K_min = max(1, floor(min_per_hour*dur_h))`, relaxar o percentil em passos (p60 → p50 → p40) até atingir `K_min` ou `S_min_abs`. `S_min_abs` impede lixo (música ambiente: `speech_density` baixa → S baixo → continua sem clipe, o que é correto). Registrar `floor_mode: true` no manifesto.
- **Diversificação:** cotas por terço do vídeo (2.5) e `min_sep` 120 s entre centros; evita 8 clipes colados num trecho.
- **Dedup:** (a) NMS temporal acima; (b) após ASR, descartar segmento cuja similaridade TF-IDF com um já escolhido > 0.6 (mesmo assunto repetido); (c) janelas que se sobrepõem > 30% fundem-se no de maior S.
- **Interação com o problema "assunto cortado":** o unidade-de-assunto do estágio B (2.1) substitui a janela fixa de 55 s centrada no pico; `snap.py` passa a receber `[seg.start, seg.end]` do assunto em vez de `peak ± 45`.

## 5. Calibração sem GPU

1. **Rótulos grátis (VOD YouTube com heatmap):** Rhapsody usa "most replayed" como verdade (https://arxiv.org/abs/2505.19429). Para VODs com heatmap, calcular o score **sem** o sinal `heat` e medir correlação (Spearman) / precision@K contra o heatmap. Dá dezenas de vídeos de calibração sem rotular nada. Ressalva: heatmap é viesado para o início do vídeo (não verificado aqui; checar antes de usar).
2. **Conjunto manual pequeno:** 20–50 momentos (metade bons, metade ruins) em 3–5 vídeos de tipos diferentes (podcast, live gameplay, Twitch). Marcar `ts_inicio,ts_fim,bom/ruim` num CSV em `tests/fixtures/`. Cerca de 1 h de trabalho humano.
3. **Métrica:** `precision@K` (K = cota por hora), mais `recall@K` dos "bons" e fração de clipes cortados no meio do assunto (já há `evaluate_clips` em `eval.py` para bordas).
4. **Aproveitar clipes já gerados:** `docs/teste-real.md` lista 8 clipes do Podpah #1049 (scores 0,539–0,902, todos com heatmap+loudness) e 2 da live Yuri. Tratar como *positivos fracos* (aceitos pelo pipeline atual); não há negativos ainda: pedir ao usuário que marque quais dos 10 são realmente bons/ruins (30 s de trabalho) e adicionar como primeiros rótulos. Os 8 candidatos descartados no snap (16 → 8) são *negativos fracos*.
5. **Ajuste de pesos:** busca em grade grosseira (3–4 valores por peso) maximizando precision@K no conjunto rotulado; custo = milissegundos, pois as features são cacheáveis em `.npz`. Cuidado com sobreajuste: com 20–50 rótulos, preferir pesos "redondos" e poucos graus de liberdade.

## 6. Desenho concreto (pequeno) e ordem de implementação

### Passos
1. **`signals.py`**: adicionar `window_features(bundle, win=60, hop=20)` → matriz `[n_janelas, features]` (loud, dyn, rate_proxy, speech_density, chat?, heat?) + `rank_pct` por coluna. Reaproveita `scores` e `rms_db`. (~60 linhas)
2. **`candidates.py`**: nova `pick_by_quota(bundle, quota_per_hour, min_per_hour, max_clips, min_sep=120, pct_floor=60)` que implementa 2.3 (S_A) + 2.5 + modo piso (seção 4). `pick_candidate_peaks` fica como fallback/feature `is_peak`. Retorna `CandidatePeak` com `start_win/end_win` = janela, `peak` = centro; **sem mudar o modelo**, só preenche mais campos (`raw_score = S_A`). Adicionar `floor_mode` ao dataclass.
3. **`asr.py`**: sem mudança de lógica; só receber mais/menos janelas. Acrescentar parada por `--asr-budget-min`.
4. **Novo `topics.py`** (≈80 linhas, sem deps novas): `segment_topics(words, block=20, ...)` TextTiling-like + `text_cheap` (regex/contagens) + `S_B`. Reordena/refina candidatos pós-ASR e dedup por TF-IDF.
5. **`snap.py::select_clips`**: aceitar `[start_win, end_win]` já ajustado ao assunto (em vez de `±45`); limites 45–60 s/partes ficam com a pesquisa de divisão. Nenhuma mudança de assinatura obrigatória; opcional passar `topic_bounds`.
6. **`cli.py`**: flags `--quota-per-hour` (padrão 4), `--min-per-hour` (padrão 1), `--asr-budget-min` (padrão derivado: 8 min × horas), `--no-llm`. Trocar a chamada na linha 201 por `pick_by_quota`; o ramo "Nenhum pico candidato" (linhas 205-226) só dispara se `S_min_abs` barrar tudo (música/silêncio). Gravar `floor_mode` e `quota` no manifesto.
7. **LLM opcional (`score_llm.py`)**: interface `score(text) -> float` com impl. padrão local (llama.cpp) e `--no-llm` desliga; rodar só após descarregar Whisper, em top-M.
8. **Calibração**: `tests/fixtures/labels.csv` + script `python -m clipes.calib` (precision@K, grade de pesos).

### Ordem
1. (1)+(2)+(6) com features só de áudio/chat/heatmap: já elimina "zero clipes" e o limite por picos. Testável com os `.npz` do `docs/teste-real.md` sem rodar ASR.
2. (5): usar janelas de assunto no snap (resolve o "assunto cortado").
3. (4): refinamento textual + dedup (sem LLM).
4. (8): calibração com heatmap + rótulos manuais; ajustar pesos.
5. (7): LLM local, só se o passo 4 mostrar ganho de precision@K.

### Riscos / não verificado
- Pesos, `pct_floor=60`, `min_sep=120`, quota padrão 4/h: valores iniciais, não derivados de fonte.
- Qualidade de `rate_proxy`/`dyn` como substitutos de "bom momento" em live calma: hipótese, só a calibração dirá.
- Risada em transcrição Whisper e velocidade de LLM 1–3B no Ryzen 5500U: não medidos.
- Funcionamento interno de seleção do ClipsAI e fatores completos do OpusClip: não verificados.
